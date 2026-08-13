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
_FN_ANCHOR_ID_RE = re.compile(r"^fn(?:ref|def)_")
_HK_FN_ID_RE = re.compile(r"\.F(\d+)(?:-|$)")
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
        anchor_id: str | None = None,
        exclude_commentary: bool = False,
    ) -> dict:
        """Parse one section into ``{heading, body_markdown, footnotes}``.

        ``body_markdown`` is the commentary only (inline ``[^n]`` markers, no
        heading line, no footnote definitions); ``footnotes`` is ``{n: text}``.
        ``html`` may be the whole page or just the content-container outerHTML.

        ``exclude_commentary`` keeps only a section's statutory text, for HK
        ordinance sections whose annotations are separate TOC leaves.
        """
        soup = BeautifulSoup(html, "lxml")
        container = self._find_container(soup)
        if container is None:
            raise ParseError("content container (.SS_contentdocument) not found")

        if anchor_id:
            container = self._isolate_anchor(
                container, anchor_id, exclude_commentary=exclude_commentary
            )

        if not heading:
            heading = self._extract_heading(soup, container)

        self._strip_chrome(container)
        self._normalize_hk_structures(container)
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
        anchor_id: str | None = None,
    ) -> str:
        """Assemble one section as a standalone Markdown document (heading + body
        + ``[^n]:`` definitions). Thin wrapper over :meth:`parse_structured`."""
        r = self.parse_structured(
            html, section_number=section_number, heading=heading, anchor_id=anchor_id
        )
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
            "h2.SS_Banner",                               # selected HK section heading
            ".SS_LegisCitatorLink",                       # "View Legislation Citator" chrome
            ".legislation-citator-icon-tooltip",
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
            num = self._footnote_definition_number(li)
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
        m = _HK_FN_ID_RE.search(a.get("id", "") or "")
        if m:
            return m.group(1)
        m2 = re.search(r"\d+", a.get_text(strip=True))
        return m2.group(0) if m2 else ""

    def _footnote_definition_number(self, li) -> str:
        num_el = li.select_one(config.SEL_FOOTNOTE_DEF_NUM) or li.select_one(
            "a.SS_FootnoteDefinition"
        )
        if not num_el:
            return ""
        return self._footnote_number(num_el) or num_el.get_text(strip=True)

    # --- sub-document slicing --------------------------------------------

    def _isolate_anchor(self, container, anchor_id: str, *,
                        exclude_commentary: bool = False):
        """Keep one anchored HK subsection out of a larger sub-document.

        A Lexis+ HK page holds many addressable units, each introduced by an
        empty ``<a id="…">`` bookmark. The ids are hierarchical, which is what
        bounds the slice: everything from the target anchor up to the next
        anchor that is *not* a descendant of it belongs to this unit. That one
        rule covers all three shapes the markup uses —

            AOHK.CAP1.S7                  a section (statutory text)
            AOHK.CAP1.S7.COMNTRY_7-02     one annotation within it
            AOHK.CAP1.S3.ADULT            one definition within a section

        — where sniffing the following element does not: only the section
        anchors are followed by a heading, definitions are followed by the
        defined term, and annotations by an ``SS_Heading`` span.

        ``exclude_commentary`` drops the annotations block, used when this node
        has TOC children of its own so each annotation is captured separately.
        """
        anchor = container.find(id=anchor_id)
        if anchor is None:
            return container

        start = self._anchor_scope_start(anchor, anchor_id)
        chunks = []
        for node in [start, *list(start.next_siblings)]:
            if node is not start and self._starts_next_unit(node, anchor_id):
                break
            chunks.append(str(node))

        section_soup = BeautifulSoup('<div class="SS_HK"></div>', "lxml")
        wrapper = section_soup.select_one("div")
        fragment = BeautifulSoup("".join(chunks), "lxml")
        source = fragment.body if fragment.body else fragment
        for child in list(source.contents):
            wrapper.append(child)

        if exclude_commentary:
            for el in wrapper.select(config.SEL_HK_COMMENTARY_BLOCK):
                el.decompose()

        refs = {
            self._footnote_number(a)
            for a in wrapper.select(config.SEL_FOOTNOTE_REF)
            if self._footnote_number(a)
        }
        if refs:
            if not wrapper.select_one(config.SEL_FOOTNOTE_FOOTER):
                for footer in container.select(config.SEL_FOOTNOTE_FOOTER):
                    if anchor_id not in str(footer):
                        continue
                    footer_copy = BeautifulSoup(str(footer), "lxml").select_one(
                        config.SEL_FOOTNOTE_FOOTER
                    )
                    if footer_copy:
                        wrapper.append(footer_copy)
                    break
            for footer in wrapper.select(config.SEL_FOOTNOTE_FOOTER):
                for li in footer.find_all("li"):
                    if self._footnote_definition_number(li) not in refs:
                        li.decompose()
                if not footer.get_text(strip=True):
                    footer.decompose()

        return wrapper

    @staticmethod
    def _is_bookmark(node) -> bool:
        """An empty ``<a id="…">`` marking an addressable unit.

        Two kinds of anchor are deliberately not bookmarks: cross-reference
        links (``SS_EmbeddedLink``), which carry an href, and Halsbury's
        footnote definitions, whose ``fndef__N_…`` ids belong to the footnote
        machinery rather than the document tree — treating one as a boundary
        would cut a section's footnote footer off from its body.
        """
        if getattr(node, "name", None) != "a" or not node.get("id"):
            return False
        if node.get("href"):
            return False
        classes = node.get("class") or []
        if any(c.startswith("SS_Footnote") for c in classes):
            return False
        return not _FN_ANCHOR_ID_RE.match(node["id"])

    @staticmethod
    def _is_descendant_id(candidate: str, ancestor: str) -> bool:
        """``AOHK.CAP1.S7.1`` is inside ``AOHK.CAP1.S7``; ``AOHK.CAP1.S70`` is not.
        The separator check is what keeps sibling sections apart."""
        return (
            candidate != ancestor
            and candidate.startswith(ancestor)
            and candidate[len(ancestor):len(ancestor) + 1] in (".", "-", "_")
        )

    def _boundary_ids(self, node, anchor_id: str) -> bool:
        """True if ``node`` is — or contains — a bookmark starting the next unit."""
        if self._is_bookmark(node) and not self._is_descendant_id(node["id"], anchor_id):
            return True
        find_all = getattr(node, "find_all", None)
        if find_all is None:
            return False
        return any(
            self._is_bookmark(a) and not self._is_descendant_id(a["id"], anchor_id)
            for a in find_all("a", id=True)
        )

    def _starts_next_unit(self, node, anchor_id: str) -> bool:
        return self._boundary_ids(node, anchor_id)

    def _anchor_scope_start(self, anchor, anchor_id: str):
        """The outermost element the anchor introduces.

        Definition anchors sit inside a ``<dt>`` whose body is the *following*
        ``<dd>``, so slicing from the anchor's own siblings would keep only the
        defined term. Climb while the anchor is the first thing in its parent
        and that parent holds nothing belonging to another unit.
        """
        node = anchor
        while True:
            parent = node.parent
            if parent is None or parent.name in ("[document]", "body", "html"):
                return node
            first = next(
                (c for c in parent.children
                 if getattr(c, "name", None) or str(c).strip()), None
            )
            if first is not node or self._boundary_ids(parent, anchor_id):
                return node
            node = parent

    def _normalize_hk_structures(self, container) -> None:
        """Give the HK annotated-ordinance markup the shape Markdown expects.

        Two things the SS_ classes encode purely visually:
          * ``SS_ListLabel`` sits flush against ``SS_ListItemContent`` with no
            whitespace between the spans, so a subsection renders as
            ``(1)Words and expressions…``. Separate them.
          * an annotation heading (``[7.01] Enactment history``) is a bold span,
            not a heading element, so the paragraph numbering that legal
            citation depends on flattens into the body text. Retag it.
        """
        for label in container.select(config.SEL_HK_LIST_LABEL):
            text = label.get_text()
            if text and not text[-1].isspace():
                label.append(" ")

        for head in container.select(config.SEL_HK_COMMENTARY_HEADING):
            head.name = "h3"
            head.attrs = {}
            # the label is a bold span inside; "### **[7.01] …**" is just noise
            head.string = self._clean_inline(head.get_text(" ", strip=True))

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
