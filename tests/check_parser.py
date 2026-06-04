"""Offline validation of the parser against the saved Lexis sample pages.

Run from the code/ directory:
    .venv/bin/python tests/check_parser.py
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/ on path

from lex.plugins.parser import SectionParser  # noqa: E402

PROJECT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = PROJECT / "Content Scraping"
OUT = pathlib.Path(__file__).resolve().parents[1] / ".state" / "parser_out"
OUT.mkdir(parents=True, exist_ok=True)

# leakage we must NOT see in clean output
CHROME = ["Halsbury's Laws of England", "TOCTrail", "Shepard", "Jump to",
          "SS_", "ng-star-inserted", "display:none"]

parser = SectionParser()
html_files = sorted(p for p in SAMPLES.glob("*.html"))
print(f"found {len(html_files)} sample pages in {SAMPLES}\n")

results = []
for path in html_files:
    html = path.read_text(encoding="utf-8", errors="replace")
    md = parser.parse(html)
    (OUT / (path.stem + ".md")).write_text(md, encoding="utf-8")

    heading = md.splitlines()[0] if md.startswith("# ") else "(no heading)"
    fn_nums = re.findall(r"^\[\^(\d+)\]:", md, flags=re.M)
    ref_nums = sorted(set(re.findall(r"\[\^(\d+)\]", md)), key=int)
    leaks = [c for c in CHROME if c in md]
    body_paras = md.split("\n\n")

    print(f"=== {path.name} ===")
    print(f"  heading      : {heading}")
    print(f"  footnotes    : {len(fn_nums)} defs -> {fn_nums}")
    print(f"  ref markers  : {ref_nums}")
    print(f"  chrome leaks : {leaks if leaks else 'none'}")
    print(f"  output bytes : {len(md)}")
    results.append((path.name, heading, fn_nums, leaks))

# --- assertions on the known §1 page ---
print("\n--- assertions ---")
s1 = next((r for r in results if r[0].startswith("1.")), None)
ok = True
if s1:
    name, heading, fn_nums, leaks = s1
    checks = [
        ("§1 heading", heading == "# 1. Nature of the relation of agency."),
        ("§1 has 12 footnotes", len(fn_nums) == 12),
        ("§1 footnotes are 1..12", fn_nums == [str(i) for i in range(1, 13)]),
        ("§1 no chrome leakage", not leaks),
    ]
    for label, passed in checks:
        print(f"  {'ok ' if passed else 'FAIL'} {label}")
        ok = ok and passed
print("\nRESULT:", "ALL OK" if ok else "FAILURES — inspect .state/parser_out/")
print("wrote markdown to:", OUT)
