"""TOC crawler: from a per-title seed URL, expand the tree and harvest every
section's contentItem URN + pdtocnodeidentifier (harvest, never synthesise).

Design split for testability:
  * ``build_sections(nodes, title)`` — pure, DOM-independent. Reconstructs each
    leaf's hierarchy from node-id *prefixes* (a payoff of the decoder), dedupes
    by URN, and sorts into reading order. Unit-tested offline.
  * ``nodes_from_html(html)`` — bs4 extraction of {nodeid, label, urn} from the
    rendered TOC. Selectors are best-effort and may need tuning on first live run.
  * ``harvest(seed_url, title)`` — Playwright: expand all nodes, then the above.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .. import config
from ..core import urls
from ..core.models import Section
from .base import Plugin

_NUM_HEAD_RE = re.compile(r"^\s*(\d+[A-Za-z]?)\.\s*(.+?)\s*$")
_BRACKET_NUM_HEAD_RE = re.compile(r"^\s*\[?(\d+(?:\.\d+)+[A-Za-z]?)\]?\.\s*(.+?)\s*$")


def split_number_heading(label: str) -> tuple[str, str]:
    m = _BRACKET_NUM_HEAD_RE.match(label or "")
    if m:
        return (m.group(1), m.group(2))
    m = _NUM_HEAD_RE.match(label or "")
    return (m.group(1), m.group(2)) if m else ("", (label or "").strip())


def build_sections(nodes: list[dict], title: str, volume: str | None = None,
                   publication: str | None = None) -> list[Section]:
    """Turn harvested TOC nodes into ordered, deduped Section records.

    ``nodes`` items: ``{"nodeid": str, "label": str, "urn": str|None, "url": str|None}``.
    Structural nodes (no URN) supply hierarchy labels; leaf nodes (with a URN)
    become Sections whose hierarchy is read off matching node-id prefixes.
    """
    label_by_id = {n["nodeid"]: (n.get("label") or "").strip()
                   for n in nodes if n.get("nodeid")}
    leaf_nodes = [
        n for n in nodes
        if n.get("urn") and n.get("nodeid") and not n.get("has_children")
    ]
    urn_counts: dict[str, int] = {}
    for n in leaf_nodes:
        urn_counts[n["urn"]] = urn_counts.get(n["urn"], 0) + 1
    seen: dict[str, Section] = {}
    for n in leaf_nodes:
        urn, nodeid = n.get("urn"), n.get("nodeid")
        section_key = f"{urn}#{nodeid}" if urn_counts.get(urn, 0) > 1 else urn
        if not urn or not nodeid or section_key in seen:
            continue
        number, heading = split_number_heading(n.get("label", ""))
        # hierarchy = labels of ancestor node-id prefixes (skip the title chunk)
        hierarchy = [
            label_by_id[nodeid[:k]]
            for k in range(6, len(nodeid), 3)
            if nodeid[:k] in label_by_id and label_by_id[nodeid[:k]]
        ]
        seen[section_key] = Section(
            urn=urn, nodeid=nodeid, title=title, number=number, heading=heading,
            hierarchy=hierarchy, volume=volume, publication=publication,
            source_url=n.get("url") or urls.build_viewer_url(
                urn, nodeid, scroll_reference_id=n.get("anchor_id")
            ),
            anchor_id=n.get("anchor_id"),
            section_key=section_key,
        )
    return sorted(seen.values(), key=lambda s: s.decoded_path)


_PAGE_MODEL_MARKER = "this.add('page.model'"
_URN_IN_PATH = re.compile(r"urn:contentItem:[0-9A-Za-z-]+")


def nodes_from_html(html: str) -> list[dict]:
    """Extract TOC nodes as ``{nodeid, label, urn, url}``.

    Primary path: parse the embedded Angular ``page.model`` JSON (present in the
    *served* HTML, so no SPA render needed). Fallback: scrape anchor/data-attr
    nodes from the DOM.
    """
    model = extract_page_model(html)
    if model is not None:
        return _nodes_from_model(model)
    return _nodes_from_dom(html)


def extract_page_model(html: str) -> dict | None:
    """Return the embedded ``page.model`` JSON object, or None if not present."""
    soup = BeautifulSoup(html, "lxml")
    for s in soup.find_all("script"):
        text = s.string or s.get_text() or ""
        i = text.find(_PAGE_MODEL_MARKER)
        if i != -1:
            try:
                return json.loads(_balanced_json(text, i))
            except (ValueError, json.JSONDecodeError):
                return None
    return None


def _balanced_json(text: str, start: int) -> str:
    """Slice the first complete ``{...}`` at/after ``start`` (string-aware)."""
    b = text.find("{", start)
    depth = 0
    instr = esc = False
    for j in range(b, len(text)):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            instr = not instr
        elif not instr:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[b:j + 1]
    raise ValueError("unbalanced page.model JSON")


def _first_list_under_key(model, key: str) -> list:
    stack = [model]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if isinstance(o.get(key), list):
                return o[key]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return []


def _has_descendant_counts(value) -> bool:
    parts = [p for p in str(value or "").split("|") if p]
    for part in parts:
        try:
            if int(part) > 0:
                return True
        except ValueError:
            return True
    return False


def iter_tocnodes(model: dict) -> list[dict]:
    """Flatten the TOC tree, computing each node's full pdtocnodeidentifier by
    concatenating per-level id chunks (the ROOT sentinel contributes none)."""
    out: list[dict] = []

    def rec(node: dict, chunks: list[str]) -> None:
        cid = node.get("id", "")
        chunks2 = chunks if cid in ("", "ROOT") else chunks + [cid]
        p = node.get("props", {}) or {}
        out.append({
            "nodeid": "".join(chunks2),
            "title": p.get("linktemplatetitle") or "",
            "islink": bool(p.get("islink")),
            "haschildren": bool(p.get("haschildren")),
            "href": p.get("linktemplatehrefvalue") or "",
            "anchor_id": p.get("anchoridref") or "",
            "nodepath": p.get("nodepath"),
            "countsbylevel": p.get("countsbylevel") or "",
            "level": p.get("level"),
        })
        for child in (node.get("collections", {}) or {}).get("nodehierarchy", []) or []:
            rec(child, chunks2)

    for n in _first_list_under_key(model, "tocnodes"):
        rec(n, [])
    return out


def _nodes_from_model(model: dict) -> list[dict]:
    nodes: list[dict] = []
    for r in iter_tocnodes(model):
        if not r["nodeid"]:
            continue  # ROOT sentinel
        urn = None
        url = None
        if r["islink"] and "/shared/document/" in r["href"]:
            m = _URN_IN_PATH.search(r["href"])
            urn = m.group(0) if m else None
            url = urls.build_viewer_url_from_doc_fullpath(
                r["href"], r["nodeid"], scroll_reference_id=r["anchor_id"] or None
            )
        nodes.append({
            "nodeid": r["nodeid"],
            "label": r["title"],
            "urn": urn,
            "url": url,
            "anchor_id": r["anchor_id"] or None,
            "has_children": r["haschildren"] or _has_descendant_counts(r["countsbylevel"]),
        })
    return nodes


def nodes_from_toctree(data: dict) -> list[dict]:
    """Parse the ``/apac/f/TocInfo/toctreeresults`` JSON response.

    Shape: ``tocEntity.tocContainer.tocNodes[]`` with recursive ``nodes[]``.
    Each node has the *full* ``nodeId`` (= pdtocnodeidentifier), ``nodeTitle``
    ("N. Heading" for leaves, Part/Group labels for branches), and
    ``docFullPath`` (``/shared/document/…/urn:contentItem:…`` for section leaves).
    """
    out: list[dict] = []

    def rec(node: dict) -> None:
        nid = node.get("nodeId") or ""
        dfp = node.get("docFullPath") or ""
        children = node.get("nodes") or []
        counts = node.get("countsByLevel") or node.get("countsbylevel") or ""
        has_children = (
            bool(children) or bool(node.get("hasChildren"))
            or _has_descendant_counts(counts)
        )
        anchor_id = (
            node.get("anchorIdRef") or node.get("anchoridref")
            or node.get("anchorId") or node.get("anchorid")
        )
        urn = None
        url = None
        if "/shared/document/" in dfp:
            m = _URN_IN_PATH.search(dfp)
            urn = m.group(0) if m else None
            url = urls.build_viewer_url_from_doc_fullpath(
                dfp, nid, scroll_reference_id=anchor_id
            )
        if nid:
            out.append({"nodeid": nid, "label": node.get("nodeTitle") or "",
                        "urn": urn, "url": url, "anchor_id": anchor_id,
                        "has_children": has_children})
        for child in children:
            rec(child)

    container = (data.get("tocEntity") or {}).get("tocContainer") or {}
    for n in container.get("tocNodes") or []:
        rec(n)
    return out


def _nodes_from_dom(html: str) -> list[dict]:
    """Fallback: scrape anchor/data-attr TOC nodes when no page.model is present."""
    soup = BeautifulSoup(html, "lxml")
    nodes: list[dict] = []
    for el in soup.select(config.SEL_TOC_NODE):
        href = el.get("href", "") or ""
        nodeid = el.get(config.TOC_NODEID_ATTR)
        urn = url = None
        if href and urls.looks_like_section_href(href):
            parsed = urls.parse_viewer_url(href)
            urn = parsed["urn"]
            nodeid = nodeid or parsed["nodeid"]
            url = href
        if not nodeid:
            continue
        nodes.append({
            "nodeid": nodeid,
            "label": el.get_text(" ", strip=True),
            "urn": urn,
            "url": url,
        })
    return nodes


_TITLE_VOL_RE = re.compile(r"^(.*?)\s*\((Volume\b.*)\)\s*$")


def split_title_volume(name: str) -> tuple[str, str | None]:
    """'Agency (Volume 1 (2022))' -> ('Agency', 'Volume 1 (2022)')."""
    m = _TITLE_VOL_RE.match(name or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else ((name or "").strip(), None)


def extract_to_level(countsbylevel: str) -> int:
    """A title's max depth = 1 + number of per-level descendant counts."""
    parts = [x for x in (countsbylevel or "").split("|") if x]
    return 1 + len(parts) if parts else config.TOC_MAX_LEVEL


class TocCrawler(Plugin):
    """Harvests sections via the replicated toctreeresults JSON API:
    GET the root TOC page once for the title catalog, then one POST per title
    returns that title's whole section subtree."""

    last_titles: list[dict] = []
    publication: str | None = None

    def list_titles(self, toc_fullpath: str = config.ROOT_TOC_FULLPATH) -> list[dict]:
        """Return the title nodes under the TOC root: {nodeid, name, countsbylevel}.
        Also records the publication name (the TOC root node's title)."""
        html = self.session.get_page_html(urls.build_toc_url(toc_fullpath))
        model = extract_page_model(html)
        titles: list[dict] = []
        if model:
            recs = iter_tocnodes(model)
            self.publication = next(
                (r["title"].strip() for r in recs if r["nodeid"] == "" and r["title"]),
                None,
            )
            titles = [{"nodeid": r["nodeid"], "name": r["title"],
                       "countsbylevel": r["countsbylevel"]}
                      for r in recs if len(r["nodeid"]) == 3]
        self.last_titles = titles
        return titles

    def harvest_title(self, toc_fullpath: str, nodeid: str, name: str,
                      level: int | None = None,
                      publication: str | None = None) -> list[Section]:
        """POST toctreeresults for one title node and build its Section list."""
        body = {
            "action": "toctreeresults",
            "tocId": urls.tocid_b64(toc_fullpath),
            "nodeId": nodeid,
            "extractToLevel": level or config.TOC_MAX_LEVEL,
            "masterFeatureContext": config.PDMFID,
        }
        data = self.session.request_json(
            config.TOCTREE_ENDPOINT, body, referer=urls.build_toc_url(toc_fullpath)
        )
        clean_title, volume = split_title_volume(name)
        return build_sections(nodes_from_toctree(data), title=clean_title,
                              volume=volume, publication=publication or self.publication)

    def harvest(self, seed_url: str, title: str | None = None,
                volume: str | None = None,
                dump_path: str | Path | None = None) -> list[Section]:
        """Harvest one title (``title`` name prefix) or every title (``title`` None)
        under the root TOC given by ``seed_url`` (defaults to the whole-work root)."""
        toc_fullpath = (urls.parse_toc_url(seed_url).get("toc_fullpath")
                        if seed_url else None) or config.ROOT_TOC_FULLPATH
        titles = self.list_titles(toc_fullpath)
        if title:
            want = title.lower()
            titles = ([t for t in titles if t["name"].lower().startswith(want)]
                      or [t for t in titles if want in t["name"].lower()])

        sections: list[Section] = []
        summary: list[str] = []
        for t in titles:
            level = extract_to_level(t["countsbylevel"])
            secs = self.harvest_title(toc_fullpath, t["nodeid"], t["name"], level,
                                      publication=self.publication)
            sections.extend(secs)
            summary.append(f"{t['nodeid']}\t{t['name']}\tsections={len(secs)}\tlevel={level}")

        self.last_titles = titles
        if dump_path:
            Path(dump_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dump_path).write_text("\n".join(summary) + "\n", encoding="utf-8")
        return sections
