"""Offline tests for viewer-URL build/parse (vs links.txt) and the manifest.

    .venv/bin/python tests/test_urls_manifest.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from lex.core import urls  # noqa: E402
from lex.core.manifest import Manifest  # noqa: E402
from lex.core.models import Section  # noqa: E402

PROJECT = pathlib.Path(__file__).resolve().parents[2]
LINKS = (PROJECT / "TOC" / "links.txt").read_text(encoding="utf-8").splitlines()
URLS = [ln.strip() for ln in LINKS if ln.strip().startswith("https")]


def test_parse_and_roundtrip_links():
    assert URLS, "no URLs found in links.txt"
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


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
    print("\nALL OK")


if __name__ == "__main__":
    _run()
