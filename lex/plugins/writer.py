"""Build the JSONL corpus for LLM/RAG use.

Layout (publication on top, per the chosen design)::

    output/<Publication>/<Title>.jsonl   # one JSON record per section, reading order
    output/corpus.jsonl                  # every record, all publications/titles

Records are rebuilt from the manifest (the source of truth), so compiling is
idempotent and safe to re-run after a resumed/partial crawl.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..core.models import Section
from .base import Plugin

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SHORTID_RE = re.compile(r"urn:contentItem:([0-9A-Za-z-]+)")

# Fields dropped from the *emitted* JSONL line — all recoverable from `breadcrumb`
# (which carries the volume), `contentitem_urn`, or `pdtocnodeidentifier`
# (source_url = build_viewer_url(urn, nodeid)). The manifest keeps the full
# record, so grouping/file paths still work and `build` can regenerate anytime.
_REDUNDANT_FIELDS = frozenset({
    "id", "publication", "title", "volume", "section_number", "heading", "hierarchy",
    "source_url",
})


def slug(text: str, max_len: int = 80) -> str:
    """Filesystem-safe, lowercased, hyphenated slug."""
    text = (text or "").replace("'", "").replace("’", "")
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


def _short_id(urn: str) -> str:
    """urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00 -> 8T8B-3B32-D6MY-P1D2."""
    m = _SHORTID_RE.search(urn or "")
    core = m.group(1) if m else (urn or "")
    return re.sub(r"-00000-00$", "", core)


class Output(Plugin):
    def __init__(self, root: Path | None = None):
        self.root = root or config.OUTPUT_DIR

    def to_record(self, section: Section, parsed: dict) -> dict:
        """Build the LLM-database record for one section."""
        body = parsed.get("body_markdown", "") or ""
        footnotes = parsed.get("footnotes", {}) or {}
        heading = parsed.get("heading") or section.heading
        last = f"{section.number}. {heading}".strip() if section.number else heading
        title_seg = (f"{section.title} ({section.volume})"
                     if section.volume else section.title)
        crumb = [section.publication, title_seg, *section.hierarchy, last]
        return {
            "id": _short_id(section.urn),
            "publication": section.publication,
            "title": section.title,
            "volume": section.volume,
            "section_number": section.number,
            "heading": heading,
            "hierarchy": list(section.hierarchy),
            "breadcrumb": " › ".join(x for x in crumb if x),
            "body_markdown": body,
            "footnotes": footnotes,
            "contentitem_urn": section.urn,
            "pdtocnodeidentifier": section.nodeid,
            "decoded_path": section.decoded_path,
            "source_url": section.source_url,
            "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "jurisdiction": config.JURISDICTION,
            "word_count": len(body.split()),
            "footnote_count": len(footnotes),
        }

    def compile_jsonl(self, records: list[dict]) -> dict:
        """(Re)write per-title JSONL files + the combined corpus.jsonl.

        Records are grouped by (publication, title) and sorted into reading order
        by ``decoded_path`` within each title.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        groups: dict[tuple[str, str], list[dict]] = {}
        for r in records:
            key = (r.get("publication") or "Unknown", r.get("title") or "Untitled")
            groups.setdefault(key, []).append(r)

        corpus_path = self.root / "corpus.jsonl"
        total = 0
        with corpus_path.open("w", encoding="utf-8") as corpus:
            for (publication, title), recs in sorted(groups.items()):
                recs.sort(key=lambda r: r.get("decoded_path") or [])
                pub_dir = self.root / slug(publication)
                pub_dir.mkdir(parents=True, exist_ok=True)
                with (pub_dir / f"{slug(title)}.jsonl").open("w", encoding="utf-8") as fh:
                    for r in recs:
                        slim = {k: v for k, v in r.items() if k not in _REDUNDANT_FIELDS}
                        line = json.dumps(slim, ensure_ascii=False)
                        fh.write(line + "\n")
                        corpus.write(line + "\n")
                        total += 1
        return {"publications": len({p for p, _ in groups}),
                "titles": len(groups), "records": total, "corpus": str(corpus_path)}
