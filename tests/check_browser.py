"""Smoke-test the browser -> selector -> outerHTML -> parser pipeline headlessly,
using the saved §1 page as a stand-in for a live render (no network/auth).

    .venv/bin/python tests/check_browser.py
"""

import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from playwright.sync_api import sync_playwright  # noqa: E402

from lex import config  # noqa: E402
from lex.plugins.parser import SectionParser  # noqa: E402

PROJECT = pathlib.Path(__file__).resolve().parents[2]
HTML = (PROJECT / "Content Scraping" / "1. Nature of the relation of agency..html").read_text(
    encoding="utf-8", errors="replace"
)

with sync_playwright() as pw:
    with tempfile.TemporaryDirectory() as profile:
        ctx = pw.chromium.launch_persistent_context(profile, headless=True)
        ctx.route("**", lambda route: route.abort())   # no subresource network
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_content(HTML, wait_until="domcontentloaded")
        page.wait_for_selector(config.SEL_CONTENT_CONTAINER, timeout=15000)
        el = page.query_selector(config.SEL_CONTENT_CONTAINER)
        outer = el.evaluate("e => e.outerHTML")         # same call as LexisSession.fetch_rendered
        ctx.close()

md = SectionParser().parse(outer, section_number="1",
                           heading="Nature of the relation of agency.")
fns = re.findall(r"^\[\^(\d+)\]:", md, re.M)
print("container outerHTML bytes :", len(outer))
print("footnotes browser->parser :", len(fns), fns)
assert md.startswith("# 1. Nature of the relation of agency."), "heading wrong"
assert len(fns) == 12, f"expected 12 footnotes, got {len(fns)}"
print("\nBROWSER PIPELINE OK")
