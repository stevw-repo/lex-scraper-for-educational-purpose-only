"""Annotated Ordinances of Hong Kong parser regression tests.

Every assertion here corresponds to a defect measured in the shipped corpus
(114,178 records) before the fix:

  * 77.8% of records were fully contained inside a sibling record — anchor
    isolation only stopped at ``h2.SS_Banner`` section headings, so an
    annotation ran on through every annotation after it;
  * the statutory text of any section that has annotations was missing
    entirely — the section is a TOC *branch* and only leaves became records;
  * 21.4% had "(1)Words…" — SS_ListLabel sits flush against SS_ListItemContent;
  * 753 carried "View Legislation Citator" chrome, 194 consisted of nothing else.

The fixture is real markup captured from the live viewer (Cap 1 Part II).

    .venv/bin/python tests/test_parser_aohk.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lex.plugins.parser import SectionParser  # noqa: E402
from lex.plugins.toc import build_sections, nodes_from_toctree  # noqa: E402

HTML = (pathlib.Path(__file__).parent / "fixtures" / "aohk_cap1_part2.html").read_text(
    encoding="utf-8"
)
DOC = "/shared/document/analytical-materials-hk/urn:contentItem:5PJX-XMB1-F1P7-B406-00000-00"


def _parse(anchor_id, exclude_commentary=False):
    return SectionParser().parse_structured(
        HTML, anchor_id=anchor_id, exclude_commentary=exclude_commentary
    )["body_markdown"]


# --- boundaries -------------------------------------------------------------

def test_annotation_stops_at_the_next_annotation():
    """The cascade bug: [7.01] used to swallow [7.02], [7.03] and [7.04]."""
    body = _parse("AOHK.CAP1.S7.COMNTRY_7-01")
    assert "came into operation on 17 December 1993" in body
    for leaked in ("[7.02]", "[7.03]", "[7.04]", "General note"):
        assert leaked not in body, f"{leaked} leaked into [7.01]"


def test_annotations_do_not_overlap_each_other():
    bodies = [_parse(f"AOHK.CAP1.S7.COMNTRY_7-0{i}") for i in (1, 2, 3, 4)]
    for i, a in enumerate(bodies):
        for j, b in enumerate(bodies):
            if i != j:
                assert a.strip() not in b, f"annotation {i} is contained in {j}"


def test_a_section_stops_at_the_next_section():
    body = _parse("AOHK.CAP1.S6", exclude_commentary=True)
    assert "Article 7 of the Basic Law" in body
    assert "masculine gender" not in body, "section 6 ran into section 7"


# --- the statutory text -----------------------------------------------------

def test_section_record_keeps_statutory_text_without_the_annotations():
    body = _parse("AOHK.CAP1.S7", exclude_commentary=True)
    assert "importing the masculine gender include the feminine" in body
    assert "[7.01]" not in body, "annotations belong to their own records"


def test_build_sections_emits_anchored_branches_as_sections():
    """An ordinance section owns the statutory text but has annotation
    children, so leaf-only harvesting dropped the legislation itself."""
    def node(nid, title, level, anchor, kids=None):
        return {"nodeId": nid, "nodeTitle": title, "level": level,
                "anchorId": anchor, "hasChildren": "yes" if kids else None,
                "docFullPath": DOC, "nodes": kids or [],
                "tocDescendantInfo": [{"nodeLevel": level + 1, "count": 1}] if kids else []}

    raw = {"tocEntity": {"tocContainer": {"tocNodes": [
        node("AACAACAAD", "PART II Interpretation", 3, None, [
            node("AACAACAADAAG", "7. Provisions for gender and number", 4,
                 "AOHK.CAP1.S7", [
                     node("AACAACAADAAGAAB", "Enactment history", 5,
                          "AOHK.CAP1.S7.COMNTRY_7-01"),
                     node("AACAACAADAAGAAC", "General note", 5,
                          "AOHK.CAP1.S7.COMNTRY_7-02"),
                 ]),
        ])
    ]}}}

    secs = build_sections(nodes_from_toctree(raw), title="CAP 1")
    by_anchor = {s.anchor_id: s for s in secs}
    assert set(by_anchor) == {
        "AOHK.CAP1.S7", "AOHK.CAP1.S7.COMNTRY_7-01", "AOHK.CAP1.S7.COMNTRY_7-02",
    }
    assert by_anchor["AOHK.CAP1.S7"].body_only is True, "section must skip annotations"
    assert by_anchor["AOHK.CAP1.S7.COMNTRY_7-01"].body_only is False
    # the structural PART node has no anchor and must stay out
    assert all(s.anchor_id for s in secs)


def test_the_whole_section_reads_once_across_its_records():
    """Statutory text and each annotation appear in exactly one record."""
    records = [
        _parse("AOHK.CAP1.S7", exclude_commentary=True),
        *[_parse(f"AOHK.CAP1.S7.COMNTRY_7-0{i}") for i in (1, 2, 3, 4)],
    ]
    # NB: probes must be unique to one record. "importing the masculine gender"
    # is not — [7.04] quotes the near-identical UK Interpretation Act 1978.
    for probe in ("feminine and neuter genders", "17 December 1993",
                  "frequently referred to in cases", "Acts Interpretation Act 1901",
                  "Interpretation Act 1978"):
        hits = sum(probe in r for r in records)
        assert hits == 1, f"{probe!r} appears in {hits} records, expected 1"


# --- text quality -----------------------------------------------------------

def test_list_labels_are_separated_from_their_content():
    body = _parse("AOHK.CAP1.S7", exclude_commentary=True)
    assert "(1) Words and expressions" in body
    assert "(1)Words" not in body


def test_legislation_citator_chrome_is_stripped():
    for anchor in ("AOHK.CAP1.S5", "AOHK.CAP1.S6", "AOHK.CAP1.S7"):
        body = _parse(anchor, exclude_commentary=True)
        assert "Legislation Citator" not in body
        assert "LegislationCitator.png" not in body


def test_annotation_heading_becomes_a_heading():
    body = _parse("AOHK.CAP1.S7.COMNTRY_7-03")
    assert body.startswith("### [7.03] Commonwealth of Australia")
    assert "**[7.03]" not in body, "heading should not also be bold"


def test_section_without_annotations_keeps_its_text():
    body = _parse("AOHK.CAP1.S5", exclude_commentary=True)
    assert "grammatical variations and cognate expressions" in body


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        print("  ok ", name)
    print("\nALL OK")


if __name__ == "__main__":
    _run()
