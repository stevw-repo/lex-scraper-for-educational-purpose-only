"""Rendered-document cache tests.

    .venv/bin/python tests/test_content_cache.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lex.core import urls  # noqa: E402
from lex.core.models import Section  # noqa: E402
from lex.plugins.content import SectionFetcher  # noqa: E402


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def fetch_rendered(self, url: str) -> str:
        self.calls.append(url)
        return f"<div class='SS_HK'>fetch {len(self.calls)}</div>"


class FakeKernel:
    def __init__(self):
        self.session = FakeSession()


def _hk_section(nodeid: str, anchor_id: str) -> Section:
    urn = "urn:contentItem:5PJX-XB01-JCBX-S3FT-00000-00"
    return Section(
        urn=urn,
        nodeid=nodeid,
        title="15 – Agency",
        number=anchor_id.removeprefix("HLHK."),
        heading="Heading",
        source_url=urls.build_viewer_url_from_doc_fullpath(
            "/shared/document/analytical-materials-hk/" + urn,
            nodeid,
            scroll_reference_id=anchor_id,
        ),
        anchor_id=anchor_id,
        section_key=f"{urn}#{nodeid}",
    )


def test_fetcher_reuses_one_rendered_document_for_hk_anchors():
    fetcher = SectionFetcher()
    fetcher.kernel = FakeKernel()

    first = fetcher.fetch_html(_hk_section("AADAACAAB", "HLHK.15.001"))
    second = fetcher.fetch_html(_hk_section("AADAACAAC", "HLHK.15.002"))

    assert first == second
    assert len(fetcher.kernel.session.calls) == 1


def _run():
    test_fetcher_reuses_one_rendered_document_for_hk_anchors()
    print("  ok  shared HK document fetched once")
    print("\nALL OK")


if __name__ == "__main__":
    _run()
