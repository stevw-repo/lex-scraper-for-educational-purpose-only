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
    section_key TEXT PRIMARY KEY,
    urn        TEXT,
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
CREATE INDEX IF NOT EXISTS ix_urn    ON sections(urn);
"""


class Manifest:
    def __init__(self, path: Path | None = None):
        self.path = path or config.MANIFEST_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(sections)")}
        if "section_key" not in cols:
            self._migrate_to_section_keys(cols)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(sections)")}
        # migrate DBs created before the `record` column existed
        if "record" not in cols:
            self.db.execute("ALTER TABLE sections ADD COLUMN record TEXT")
        self.db.commit()

    def _migrate_to_section_keys(self, cols: set[str]) -> None:
        """Move older manifests from ``urn`` primary keys to section keys."""
        self.db.execute("ALTER TABLE sections RENAME TO sections_old")
        self.db.execute("DROP INDEX IF EXISTS ix_status")
        self.db.execute("DROP INDEX IF EXISTS ix_title")
        self.db.execute("DROP INDEX IF EXISTS ix_urn")
        self.db.executescript(_SCHEMA)
        record_expr = "record" if "record" in cols else "NULL"
        self.db.execute(
            f"""INSERT OR REPLACE INTO sections
                    (section_key, urn, nodeid, title, number, heading, source_url,
                     status, path, record, error, updated_at)
                SELECT urn, urn, nodeid, title, number, heading, source_url,
                       status, path, {record_expr}, error, updated_at
                FROM sections_old"""
        )
        self.db.execute("DROP TABLE sections_old")

    # --- recording --------------------------------------------------------

    def record_harvested(self, section: Section) -> None:
        """Insert a harvested section as pending (no-op if already present)."""
        if section.key != section.urn:
            self.db.execute(
                "DELETE FROM sections WHERE section_key=? AND urn=? AND status!='done'",
                (section.urn, section.urn),
            )
        self.db.execute(
            """INSERT INTO sections (section_key, urn, nodeid, title, number,
                                     heading, source_url, status, updated_at)
               VALUES (?,?,?,?,?,?,?, 'pending', ?)
               ON CONFLICT(section_key) DO UPDATE SET
                   nodeid=excluded.nodeid, title=excluded.title,
                   number=excluded.number, heading=excluded.heading,
                   source_url=excluded.source_url
               WHERE sections.status != 'done'""",
            (section.key, section.urn, section.nodeid, section.title, section.number,
             section.heading, section.source_url, time.time()),
        )
        self.db.commit()

    def mark_done(self, section_key: str, record: dict | None = None) -> None:
        self.db.execute(
            """UPDATE sections
               SET status='done', record=?, error=NULL, updated_at=?
               WHERE section_key=?""",
            (json.dumps(record, ensure_ascii=False) if record is not None else None,
             time.time(), section_key),
        )
        self.db.commit()

    def failed_urns(self) -> set[str]:
        """Keys of sections currently marked failed (targets for a `retry` run)."""
        rows = self.db.execute(
            "SELECT section_key FROM sections WHERE status='failed'"
        ).fetchall()
        return {r["section_key"] for r in rows}

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

    def mark_failed(self, section_key: str, error: str) -> None:
        self.db.execute(
            "UPDATE sections SET status='failed', error=?, updated_at=? WHERE section_key=?",
            (error[:1000], time.time(), section_key),
        )
        self.db.commit()

    # --- queries ----------------------------------------------------------

    def is_done(self, section_key: str) -> bool:
        row = self.db.execute(
            "SELECT status FROM sections WHERE section_key=?", (section_key,)
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
