"""Markdown corpus writer tests.

    .venv/bin/python tests/test_writer_markdown.py
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lex.plugins.writer import Output  # noqa: E402

PUB = "Annotated Ordinances of Hong Kong"


def _record(hierarchy, number, heading, body, path, footnotes=None):
    return {
        "publication": PUB, "title": "CAP 1", "volume": None,
        "section_number": number, "heading": heading, "hierarchy": hierarchy,
        "body_markdown": body, "footnotes": footnotes or {},
        "decoded_path": path, "jurisdiction": "Hong Kong",
        "retrieved_at": "2026-08-13T00:00:00Z",
    }


RECORDS = [
    _record(["PART II"], "7", "Provisions for gender and number",
            "* (1) Words and expressions importing the masculine gender.", [1, 1]),
    _record(["PART II", "7. Provisions for gender and number"], "",
            "Enactment history",
            "### [7.01] Enactment history\n\nAmended in 1993.", [1, 1, 1]),
    _record(["PART II", "7. Provisions for gender and number"], "",
            "General note", "### [7.02] General note\n\nOften cited.", [1, 1, 2]),
]


def _write(records):
    out = Output(root=pathlib.Path(tempfile.mkdtemp()))
    stats = out.compile_markdown(records)
    text = (out.root / "annotated-ordinances-of-hong-kong" / "cap-1.md").read_text(
        encoding="utf-8"
    )
    return stats, text


def test_writes_one_file_per_title():
    stats, _ = _write(RECORDS)
    assert stats == {"publications": 1, "titles": 1, "records": 3, "files": 1,
                     "dir": stats["dir"]}


def test_section_heading_is_not_repeated_for_each_annotation():
    """The annotations carry the section as a hierarchy label, so a naive
    writer emits '### 7. Provisions…' once per annotation."""
    _, text = _write(RECORDS)
    assert text.count("# 7. Provisions for gender and number") == 1


def test_annotations_nest_under_their_section():
    _, text = _write(RECORDS)
    assert "### 7. Provisions for gender and number" in text
    assert "#### [7.01] Enactment history" in text
    assert "#### [7.02] General note" in text


def test_paragraph_number_is_promoted_into_the_heading():
    """'### [7.01] Enactment history' becomes the heading rather than being
    repeated below one reading 'Enactment history'."""
    _, text = _write(RECORDS)
    assert "#### Enactment history" not in text
    assert text.count("[7.01] Enactment history") == 1


def test_reading_order_follows_decoded_path():
    _, text = _write(list(reversed(RECORDS)))
    assert text.index("masculine gender") < text.index("[7.01]") < text.index("[7.02]")


def test_footnotes_are_appended():
    recs = [_record(["PART II"], "1", "Short title", "Body[^1].", [1, 1],
                    footnotes={"1": "A note."})]
    _, text = _write(recs)
    assert "[^1]: A note." in text


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        print("  ok ", name)
    print("\nALL OK")


if __name__ == "__main__":
    _run()
