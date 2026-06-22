"""HK sub-document parser regression tests.

    .venv/bin/python tests/test_parser_hk.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lex.plugins.parser import SectionParser  # noqa: E402


HTML = """
<div class="SS_HK">
  <header class="SS_DocumentHeader"><h1 id="SS_DocumentTitle">(1) Nature</h1></header>
  <a id="HLHK.15.001"></a><br />
  <h2 class="SS_Banner">[15.001] Nature of the relation of agency</h2>
  <p class="SS_IndentFirstLine">First section text<a class="SS_FootnoteReference"
     id="fnref_HLHK.15.001.F1-R" href="#"></a>.</p>
  <footer class="SS_Footnote">
    <ol>
      <li><span><a class="SS_FootnoteDefinition" id="fndef_HLHK.15.001.F1">1</a></span>
          <span class="SS_FootnoteBody">First note.</span></li>
    </ol>
  </footer>
  <a id="HLHK.15.002"></a><br />
  <h2 class="SS_Banner">[15.002] Other uses of the word agent</h2>
  <p>Second section text.</p>
</div>
"""


def test_hk_anchor_isolates_one_section_and_its_footnotes():
    parsed = SectionParser().parse_structured(
        HTML,
        section_number="15.001",
        heading="Nature of the relation of agency",
        anchor_id="HLHK.15.001",
    )
    assert "First section text[^1]." in parsed["body_markdown"]
    assert "Second section text" not in parsed["body_markdown"]
    assert parsed["footnotes"] == {"1": "First note."}


def _run():
    test_hk_anchor_isolates_one_section_and_its_footnotes()
    print("  ok  HK anchor section slicing")
    print("\nALL OK")


if __name__ == "__main__":
    _run()
