"""SQLite manifest for resume + dedupe + provenance.

Operational state only (lives under ``.state/``, not in the Markdown corpus).
Keyed by the contentItem URN — the stable dedupe key.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .. import config
from .models import Section

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    urn        TEXT PRIMARY KEY,
    nodeid     TEXT,
    title      TEXT,
    number     TEXT,
    heading    TEXT,
    source_url TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    path       TEXT,
    record     TEXT,                               -- the full JSON record (source of truth)
    error      TEXT,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_status ON sections(status);
CREATE INDEX IF NOT EXISTS ix_title  ON sections(title);
"""


class Manifest:
    def __init__(self, path: Path | None = None):
        self.path = path or config.MANIFEST_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        # migrate DBs created before the `record` column existed
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(sections)")}
        if "record" not in cols:
            self.db.execute("ALTER TABLE sections ADD COLUMN record TEXT")
        self.db.commit()

    # --- recording --------------------------------------------------------

    def record_harvested(self, section: Section) -> None:
        """Insert a harvested section as pending (no-op if already present)."""
        self.db.execute(
            """INSERT INTO sections (urn, nodeid, title, number, heading, source_url,
                                     status, updated_at)
               VALUES (?,?,?,?,?,?, 'pending', ?)
               ON CONFLICT(urn) DO UPDATE SET
                   nodeid=excluded.nodeid, title=excluded.title,
                   number=excluded.number, heading=excluded.heading,
                   source_url=excluded.source_url
               WHERE sections.status != 'done'""",
            (section.urn, section.nodeid, section.title, section.number,
             section.heading, section.source_url, time.time()),
        )
        self.db.commit()

    def mark_done(self, urn: str, record: dict | None = None) -> None:
        self.db.execute(
            "UPDATE sections SET status='done', record=?, error=NULL, updated_at=? WHERE urn=?",
            (json.dumps(record, ensure_ascii=False) if record is not None else None,
             time.time(), urn),
        )
        self.db.commit()

    def failed_urns(self) -> set[str]:
        """URNs of sections currently marked failed (targets for a `retry` run)."""
        rows = self.db.execute(
            "SELECT urn FROM sections WHERE status='failed'"
        ).fetchall()
        return {r["urn"] for r in rows}

    def done_records(self) -> list[dict]:
        """Every finished section's JSON record (the JSONL source of truth)."""
        rows = self.db.execute(
            "SELECT record FROM sections WHERE status='done' AND record IS NOT NULL"
        ).fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["record"]))
            except (ValueError, TypeError):
                pass
        return out

    def mark_failed(self, urn: str, error: str) -> None:
        self.db.execute(
            "UPDATE sections SET status='failed', error=?, updated_at=? WHERE urn=?",
            (error[:1000], time.time(), urn),
        )
        self.db.commit()

    # --- queries ----------------------------------------------------------

    def is_done(self, urn: str) -> bool:
        row = self.db.execute(
            "SELECT status FROM sections WHERE urn=?", (urn,)
        ).fetchone()
        return bool(row) and row["status"] == "done"

    def pending(self, title: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM sections WHERE status!='done'"
        args: tuple = ()
        if title:
            sql += " AND title=?"
            args = (title,)
        return self.db.execute(sql, args).fetchall()

    def counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) n FROM sections GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def title_counts(self) -> dict[str, dict]:
        """Per-title ``{done, failed, pending, total}`` (keyed by the clean title)."""
        rows = self.db.execute(
            "SELECT title, status, COUNT(*) n FROM sections GROUP BY title, status"
        ).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            d = out.setdefault(r["title"] or "Unknown",
                               {"done": 0, "failed": 0, "pending": 0, "total": 0})
            d[r["status"]] = d.get(r["status"], 0) + r["n"]
            d["total"] += r["n"]
        return out

    def close(self) -> None:
        self.db.close()
