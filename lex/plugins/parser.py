"""HTML -> Markdown parser for a single Halsbury's section.

Grounded in the real Lexis ``SS_`` markup (verified against the saved sample
pages). Produces Markdown only:

    # <section number + heading>

    <body commentary, with inline [^n] footnote markers exactly in place>

    [^1]: <footnote text>
    [^2]: ...

Footnote numbers are read from the ``SS_FootnoteReference`` / ``SS_FootnoteDefinition``
ids (``fnref__N_…`` / ``fndef__N_…``) and the ``SS_FootnoteDefinition_Content``
span -- never from a guessed ``<sup>``. Inline markers are swapped for a private
``{FN-n}`` token *before* markdownify runs (so its escaping can't mangle them),
then restored to ``[^n]`` in post-processing.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from .. import config
from ..core.exceptions import ParseError
from .base import Plugin

_FN_ID_RE = re.compile(r"fn(?:ref|def)__(\d+)_")
_PLACEHOLDER_RE = re.compile(r"\{FN-(\d+)\}")
_HSPACE = r"[^\S\n]"                      # horizontal whitespace incl. nbsp, not newline
_HSPACE_RE = re.compile(_HSPACE + r"+")
_MULTINL_RE = re.compile(r"\n{3,}")

_MD_OPTS = dict(
    heading_style=config.MD_HEADING_STYLE,
    escape_asterisks=False,
    escape_underscores=False,
    escape_misc=False,
    strip=["script", "style"],
)


def _md(node) -> str:
    """Markdownify a bs4 node's inner HTML with legal-text-friendly escaping off."""
    return MarkdownConverter(**_MD_OPTS).convert(str(node))


class SectionParser(Plugin):
    # --- public API -------------------------------------------------------

    def parse_structured(
        self,
        html: str,
        *,
        section_number: str | None = None,
        heading: str | None = None,
    ) -> dict:
        """Parse one section into ``{heading, body_markdown, footnotes}``.

        ``body_markdown`` is the commentary only (inline ``[^n]`` markers, no
        heading line, no footnote definitions); ``footnotes`` is ``{n: text}``.
        ``html`` may be the whole page or just the content-container outerHTML.
        """
        soup = BeautifulSoup(html, "lxml")
        container = self._find_container(soup)
        if container is None:
            raise ParseError("content container (.SS_contentdocument) not found")

        if not heading:
            heading = self._extract_heading(soup, container)

        self._strip_chrome(container)
        self._normalize_emphasis(container)
        footnotes = self._extract_footnotes(container)   # ordered [(num, md_text)]
        self._replace_inline_refs(container)
        self._unwrap_anchors(container)
        self._handle_drafting_notes(container)

        body_md = self._postprocess(_md(container))
        return {
            "heading": heading,
            "body_markdown": body_md,
            "footnotes": {n: t for n, t in footnotes},
        }

    def parse(
        self,
        html: str,
        *,
        section_number: str | None = None,
        heading: str | None = None,
    ) -> str:
        """Assemble one section as a standalone Markdown document (heading + body
        + ``[^n]:`` definitions). Thin wrapper over :meth:`parse_structured`."""
        r = self.parse_structured(html, section_number=section_number, heading=heading)
        return self._assemble(
            section_number, r["heading"], r["body_markdown"], list(r["footnotes"].items())
        )

    # --- container / heading ---------------------------------------------

    def _find_container(self, soup):
        els = soup.select(config.SEL_CONTENT_CONTAINER)
        if not els:
            return None
        # the real document body has the footnotes footer / the most text
        els.sort(
            key=lambda e: (
                e.select_one(config.SEL_FOOTNOTE_FOOTER) is not None,
                len(e.get_text(strip=True)),
            ),
            reverse=True,
        )
        return els[0]

    def _extract_heading(self, soup, container) -> str:
        dt = soup.select_one(config.SEL_DOC_TITLE)
        if dt and dt.get_text(strip=True):
            return self._clean_inline(dt.get_text(" ", strip=True))
        hdr = container.select_one("header.SS_DocumentHeader") or container.find(
            ["h1", "h2"]
        )
        if hdr:
            return self._clean_inline(hdr.get_text(" ", strip=True))
        return ""

    # --- chrome removal ---------------------------------------------------

    def _strip_chrome(self, container) -> None:
        selectors = [
            "script", "style", "noscript",
            "header.SS_DocumentHeader",                  # in-doc title block
            "h2.SS_HideShowSection", ".SS_Expandable",   # "Heading" toggle
            "ul.SS_TOCTrail",                            # breadcrumb
            "i.icon", ".la-JumpUp",                      # footnote jump-up icons
            "h1.SS_Heading1",                            # part/group/section headings
            '[style*="display:none"]', '[style*="display: none"]',
            "[hidden]", '[aria-hidden="true"]',
        ]
        for sel in selectors:
            for el in container.select(sel):
                el.decompose()

    # --- footnotes --------------------------------------------------------

    def _extract_footnotes(self, container) -> list[tuple[str, str]]:
        footer = container.select_one(config.SEL_FOOTNOTE_FOOTER)
        if not footer:
            return []
        out: list[list] = []  # [num, text]
        for li in footer.find_all("li"):
            num_el = li.select_one(config.SEL_FOOTNOTE_DEF_NUM)
            num = num_el.get_text(strip=True) if num_el else ""
            body = li.select_one(config.SEL_FOOTNOTE_BODY) or li
            for a in body.select("a"):
                a.unwrap()  # drop the link wrapper but keep its text + emphasis
            text = self._postprocess(_md(body)).strip()
            if num:
                out.append([num, text])
            elif text and out:
                out[-1][1] += "\n    " + text  # continuation of the previous footnote
        footer.decompose()
        return [(n, t) for n, t in out]

    # --- inline markers / links / notes ----------------------------------

    def _replace_inline_refs(self, container) -> None:
        for a in container.select(config.SEL_FOOTNOTE_REF):
            n = self._footnote_number(a)
            a.replace_with(f"{{FN-{n}}}" if n else "")

    @staticmethod
    def _footnote_number(a) -> str:
        m = _FN_ID_RE.search(a.get("id", "") or "")
        if m:
            return m.group(1)
        m2 = re.search(r"\d+", a.get_text(strip=True))
        return m2.group(0) if m2 else ""

    def _normalize_emphasis(self, container) -> None:
        """Retag Lexis house-style emphasis spans so markdownify keeps them.

        Case names/citations use ``SS_it`` (EMPHASIS_it); bold uses ``SS_bf``.
        Small-caps/caps have no clean Markdown form and are left as plain text.
        """
        for el in container.select(".SS_it, [data-housestyle='EMPHASIS_it']"):
            el.name = "em"
        for el in container.select(".SS_bf, [data-housestyle='EMPHASIS_bf']"):
            el.name = "strong"

    def _unwrap_anchors(self, container) -> None:
        for a in container.select("a"):
            a.unwrap()  # keep cross-reference text (and any emphasis), drop the link

    def _handle_drafting_notes(self, container) -> None:
        for el in container.select(config.SEL_DRAFTING_NOTE):
            if config.KEEP_DRAFTING_NOTES:
                el.name = "blockquote"
            else:
                el.decompose()

    # --- text tidy / assembly --------------------------------------------

    def _clean_inline(self, text: str) -> str:
        return _HSPACE_RE.sub(" ", text).strip()

    def _postprocess(self, text: str) -> str:
        text = _PLACEHOLDER_RE.sub(r"[^\1]", text)
        # attach the marker to the preceding word and tighten punctuation
        text = re.sub(_HSPACE + r"+(\[\^\d+\])", r"\1", text)
        text = re.sub(r"(\[\^\d+\])" + _HSPACE + r"+([,.;:)\]])", r"\1\2", text)
        text = re.sub(_HSPACE + r"*\n" + _HSPACE + r"*", "\n", text)
        text = _HSPACE_RE.sub(" ", text)
        text = _MULTINL_RE.sub("\n\n", text)
        return text.strip()

    def _assemble(self, section_number, heading, body_md, footnotes) -> str:
        title = (heading or "").strip()
        if section_number:
            sn = str(section_number).strip().rstrip(".")
            if not re.match(rf"^{re.escape(sn)}\b", title):
                title = f"{sn}. {title}".strip()
        parts: list[str] = []
        if title:
            parts.append(f"# {title}")
        if body_md:
            parts.append(body_md)
        if footnotes:
            parts.append("\n".join(f"[^{n}]: {t}" for n, t in footnotes))
        return "\n\n".join(parts).rstrip() + "\n"
