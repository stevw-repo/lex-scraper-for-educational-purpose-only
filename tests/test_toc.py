"""Offline tests for TOC node -> Section building (decode-driven hierarchy).

    .venv/bin/python tests/test_toc.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from lex.plugins.toc import build_sections, nodes_from_html, split_number_heading  # noqa: E402

# structural nodes (no urn) + leaf nodes (with urn), mimicking the Agency TOC
NODES = [
    {"nodeid": "AABAAC", "label": "1. Nature and Formation"},
    {"nodeid": "AABAACAAB", "label": "(1) The Relation of Agency"},
    {"nodeid": "AABAACAAC", "label": "(2) Competency of Parties"},
    {"nodeid": "AABAACAACAAB", "label": "(i) Competency of Principals"},
    {"nodeid": "AABAACAABAAB", "label": "1. Nature of the relation of agency.",
     "urn": "urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00"},
    {"nodeid": "AABAACAABAAC", "label": "2. Other uses of the word 'agent'.",
     "urn": "urn:contentItem:8T8B-3B32-D6MY-P1D3-00000-00"},
    {"nodeid": "AABAACAACAABAAB", "label": "3. General rule as to competency of principals.",
     "urn": "urn:contentItem:8T8B-3B32-D6MY-P1D4-00000-00"},
]


def test_split_number_heading():
    assert split_number_heading("301. Tenancies beginning on or after 1995.") == (
        "301", "Tenancies beginning on or after 1995.")
    assert split_number_heading("1. Nature of the relation of agency.") == (
        "1", "Nature of the relation of agency.")
    assert split_number_heading("(1) The Relation of Agency") == (
        "", "(1) The Relation of Agency")


def test_build_sections_hierarchy_order_dedupe():
    secs = build_sections(NODES + [NODES[4]], title="Agency", volume="Volume 1 (2022)")
    assert [s.number for s in secs] == ["1", "2", "3"]          # reading order, deduped
    s1, s2, s3 = secs
    assert s1.hierarchy == ["1. Nature and Formation", "(1) The Relation of Agency"]
    assert s1.heading == "Nature of the relation of agency."
    assert s3.hierarchy == ["1. Nature and Formation", "(2) Competency of Parties",
                            "(i) Competency of Principals"]
    assert s1.source_url and "pdtocnodeidentifier=AABAACAABAAB" in s1.source_url
    assert s1.volume == "Volume 1 (2022)"


def test_nodes_from_html_roundtrip():
    html = (
        '<div data-pdtocnodeidentifier="AABAAC">1. Nature and Formation</div>'
        '<div data-pdtocnodeidentifier="AABAACAAB">(1) The Relation of Agency</div>'
        '<a href="https://plus-lexis-com.eproxy.lib.hku.hk/apac/document/?pdmfid=1539266'
        '&pddocfullpath=%2Fshared%2Fdocument%2Fanalytical-materials-uk%2F'
        'urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00&pdtocnodeidentifier=AABAACAABAAB">'
        '1. Nature of the relation of agency.</a>'
    )
    secs = build_sections(nodes_from_html(html), title="Agency")
    assert len(secs) == 1
    assert secs[0].urn == "urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00"
    assert secs[0].hierarchy == ["1. Nature and Formation", "(1) The Relation of Agency"]
    assert secs[0].number == "1"


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        print("  ok ", name)
    print("\nALL OK")


if __name__ == "__main__":
    _run()
