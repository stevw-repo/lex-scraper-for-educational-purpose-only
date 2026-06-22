"""Offline tests for viewer-URL build/parse (vs links.txt) and the manifest.

    .venv/bin/python tests/test_urls_manifest.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from lex.core import urls  # noqa: E402
from lex.core.manifest import Manifest  # noqa: E402
from lex.core.models import Section  # noqa: E402

PROJECT = pathlib.Path(__file__).resolve().parents[1]
LINKS_PATH = PROJECT / "TOC" / "links.txt"
LINKS = (LINKS_PATH.read_text(encoding="utf-8").splitlines()
         if LINKS_PATH.exists() else [])
URLS = [ln.strip() for ln in LINKS if ln.strip().startswith("https")]


def test_parse_and_roundtrip_links():
    if not URLS:
        print("  skip links.txt roundtrip fixture (TOC/links.txt not present)")
        return
    for url in URLS:
        got = urls.parse_viewer_url(url)
        assert got["urn"] and got["urn"].startswith("urn:contentItem:"), url
        assert got["nodeid"] and len(got["nodeid"]) % 3 == 0, url
        # rebuild from harvested fields and re-parse — must match
        rebuilt = urls.build_viewer_url(got["urn"], got["nodeid"], pdmfid=got["pdmfid"])
        again = urls.parse_viewer_url(rebuilt)
        assert again["urn"] == got["urn"], (url, rebuilt)
        assert again["nodeid"] == got["nodeid"], (url, rebuilt)
    print(f"  ok  parsed + round-tripped {len(URLS)} links")


def test_build_matches_links_txt_shape():
    # §1 known values
    u = urls.build_viewer_url("urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00", "AABAACAABAAB")
    assert "pddocfullpath=%2Fshared%2Fdocument%2Fanalytical-materials-uk%2Furn:contentItem:" in u
    assert "pdtocnodeidentifier=AABAACAABAAB" in u
    assert "crid" not in u and "pdscrollreferenceid" not in u
    print("  ok  built URL matches links.txt shape (no UI-state params)")


def test_build_preserves_hk_docfullpath_and_scrollref():
    u = urls.build_viewer_url_from_doc_fullpath(
        "/shared/document/analytical-materials-hk/"
        "urn:contentItem:5PJX-XB01-JCBX-S3FT-00000-00",
        "AADAACAAB",
        scroll_reference_id="HLHK.15.001",
    )
    got = urls.parse_viewer_url(u)
    assert got["urn"] == "urn:contentItem:5PJX-XB01-JCBX-S3FT-00000-00"
    assert got["nodeid"] == "AADAACAAB"
    assert got["doc_fullpath"].startswith("/shared/document/analytical-materials-hk/")
    assert got["scroll_reference_id"] == "HLHK.15.001"
    assert "analytical-materials-hk" in u
    print("  ok  HK docFullPath + scroll ref preserved")


def test_manifest_resume_cycle():
    db_path = pathlib.Path(__file__).resolve().parents[1] / ".state" / "test_manifest.sqlite"
    if db_path.exists():
        db_path.unlink()
    m = Manifest(db_path)
    s = Section(urn="urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00", nodeid="AABAACAABAAB",
                title="Agency", number="1", heading="Nature of the relation of agency.")
    m.record_harvested(s)
    assert not m.is_done(s.urn)
    assert len(m.pending("Agency")) == 1
    m.record_harvested(s)  # idempotent
    assert m.counts().get("pending") == 1
    m.mark_done(s.urn, "output/agency/.../0001-...md")
    assert m.is_done(s.urn)
    m.record_harvested(s)  # must not revert a done row to pending
    assert m.is_done(s.urn)
    assert m.pending("Agency") == []
    assert m.counts().get("done") == 1
    m.close()
    db_path.unlink()
    print("  ok  manifest harvest/resume/dedupe cycle")


def test_manifest_allows_shared_urn_section_keys():
    db_path = pathlib.Path(__file__).resolve().parents[1] / ".state" / "test_manifest.sqlite"
    if db_path.exists():
        db_path.unlink()
    m = Manifest(db_path)
    urn = "urn:contentItem:5PJX-XB01-JCBX-S3FT-00000-00"
    s1 = Section(urn=urn, nodeid="AADAACAAB", title="15 – Agency", number="15.001",
                 heading="Nature of the relation of agency.",
                 section_key=f"{urn}#AADAACAAB")
    s2 = Section(urn=urn, nodeid="AADAACAAC", title="15 – Agency", number="15.002",
                 heading="Other uses of the word agent.",
                 section_key=f"{urn}#AADAACAAC")
    m.record_harvested(s1)
    m.record_harvested(s2)
    assert len(m.pending("15 – Agency")) == 2
    m.mark_done(s1.key, {"contentitem_urn": urn, "pdtocnodeidentifier": s1.nodeid})
    assert m.is_done(s1.key)
    assert not m.is_done(s2.key)
    m.close()
    db_path.unlink()
    print("  ok  manifest stores duplicate URNs by section key")


def test_manifest_migrates_urn_primary_key_schema():
    db_path = pathlib.Path(__file__).resolve().parents[1] / ".state" / "test_manifest.sqlite"
    if db_path.exists():
        db_path.unlink()
    import sqlite3

    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE sections (
            urn TEXT PRIMARY KEY, nodeid TEXT, title TEXT, number TEXT,
            heading TEXT, source_url TEXT, status TEXT, path TEXT,
            record TEXT, error TEXT, updated_at REAL
        );
        INSERT INTO sections
            (urn, nodeid, title, number, heading, source_url, status)
        VALUES
            ('urn:contentItem:OLD', 'AAB', 'Agency', '1', 'Heading', 'url', 'pending');
    """)
    db.close()

    m = Manifest(db_path)
    cols = {r[1] for r in m.db.execute("PRAGMA table_info(sections)")}
    assert "section_key" in cols
    assert len(m.pending("Agency")) == 1
    assert m.pending("Agency")[0]["section_key"] == "urn:contentItem:OLD"
    m.close()
    db_path.unlink()
    print("  ok  old manifest schema migrates to section_key")


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
    print("\nALL OK")


if __name__ == "__main__":
    _run()
