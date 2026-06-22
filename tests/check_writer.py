"""Offline check: parse the sample pages -> records -> JSONL corpus.

    .venv/bin/python tests/check_writer.py
"""

import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from lex.core.models import Section  # noqa: E402
from lex.plugins.parser import SectionParser  # noqa: E402
from lex.plugins.writer import Output  # noqa: E402

PROJECT = pathlib.Path(__file__).resolve().parents[2]
SAMPLES = PROJECT / "Content Scraping"
OUT = pathlib.Path(__file__).resolve().parents[1] / ".state" / "writer_out"
PUB = "Halsbury's Laws of England"
NF = ["1. Nature and Formation"]

SECTIONS = [
    Section(urn="urn:contentItem:8T8B-3B32-D6MY-P1D2-00000-00", nodeid="AABAACAABAAB",
            title="Agency", number="1", heading="Nature of the relation of agency.",
            hierarchy=NF + ["(1) The Relation of Agency"], volume="Volume 1 (2022)",
            publication=PUB),
    Section(urn="urn:contentItem:8T8B-3B32-D6MY-P1D3-00000-00", nodeid="AABAACAABAAC",
            title="Agency", number="2", heading="Other uses of the word 'agent'.",
            hierarchy=NF + ["(1) The Relation of Agency"], volume="Volume 1 (2022)",
            publication=PUB),
    Section(urn="urn:contentItem:8T8B-3B32-D6MY-P1D4-00000-00", nodeid="AABAACAACAABAAB",
            title="Agency", number="3", heading="General rule as to competency of principals.",
            hierarchy=NF + ["(2) Competency of Parties", "(i) Competency of Principals"],
            volume="Volume 1 (2022)", publication=PUB),
]


def sample_for(number: str) -> str:
    p = next((p for p in SAMPLES.glob("*.html") if p.name.startswith(f"{number}.")), None)
    if p is None:
        raise FileNotFoundError(f"sample page for section {number} not found in {SAMPLES}")
    return p.read_text(encoding="utf-8", errors="replace")


if OUT.exists():
    shutil.rmtree(OUT)

if not any(SAMPLES.glob("*.html")):
    print(f"skip writer check: no sample pages in {SAMPLES}")
    raise SystemExit(0)

parser = SectionParser()
writer = Output(root=OUT)
records = []
for s in SECTIONS:
    parsed = parser.parse_structured(sample_for(s.number),
                                     section_number=s.number, heading=s.heading)
    records.append(writer.to_record(s, parsed))

stats = writer.compile_jsonl(records)
print("compile stats:", stats)

print("\n=== output tree ===")
for p in sorted(OUT.rglob("*")):
    print("  " + str(p.relative_to(OUT)))

lines = (OUT / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
print(f"\ncorpus.jsonl lines: {len(lines)}")
print("\n=== first record (body/footnotes trimmed) ===")
rec = json.loads(lines[0])
rec["body_markdown"] = rec["body_markdown"][:180] + "…"
rec["footnotes"] = {k: (v[:55] + "…") for k, v in list(rec["footnotes"].items())[:2]}
print(json.dumps(rec, ensure_ascii=False, indent=2))
