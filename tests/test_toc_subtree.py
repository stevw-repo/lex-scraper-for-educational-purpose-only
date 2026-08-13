"""Offline tests for TocCrawler.fetch_subtree — the split-and-drill fetch used
when the gateway can't build a whole title's subtree in one request.

The fake below mirrors the real toctreeresults contract, which is the part that
took four live runs to pin down:
  * ``extractToLevel`` is an ABSOLUTE tree level, not a relative depth;
  * it must equal the target node's own deepest descendant level — asking deeper
    is an HTTP 500 ("not a valid Base-64 string"), asking shallower truncates;
  * ``hasChildren`` is the string ``"yes"`` or null, never a bool;
  * a node's ceiling is the deepest ``tocDescendantInfo.nodeLevel``.

    .venv/bin/python tests/test_toc_subtree.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/

from lex import config  # noqa: E402
from lex.core.exceptions import ContentNotFound  # noqa: E402
from lex.plugins.toc import TocCrawler, build_sections, nodes_from_toctree  # noqa: E402

TOC = "/shared/tableofcontents/urn:contentItem:5PHX-3T01-FDB0-J000-00000-00"
DOC = "/shared/document/analytical-materials-hk/urn:contentItem:"

# AAF ─ AAFAAB              leaf, level 2          (Cap 6 "Introduction")
#     ├ AAFAAC ─ …          branch 3 levels deep   (Cap 6 "CHAPTER 6")
#     └ AAFAAD ─ …          branch 4 levels deep   (Cap 6 "BANKRUPTCY RULES")
TREE = {
    "AAF":             {"title": "BANKRUPTCY ORDINANCE (CAP 6)", "kids": ["AAFAAB", "AAFAAC", "AAFAAD"]},
    "AAFAAB":          {"title": "Introduction", "kids": [], "urn": "5XJ3-B"},
    "AAFAAC":          {"title": "CHAPTER 6", "kids": ["AAFAACAAB"]},
    "AAFAACAAB":       {"title": "1. Short title", "kids": [], "urn": "5XJ3-C1"},
    "AAFAAD":          {"title": "BANKRUPTCY RULES (CAP 6A)", "kids": ["AAFAADAAB"]},
    "AAFAADAAB":       {"title": "Part I", "kids": ["AAFAADAABAAB"]},
    "AAFAADAABAAB":    {"title": "2. Interpretation", "kids": [], "urn": "5XJ3-D1"},
}


def _level(nid):
    return len(nid) // 3


def _max_level(nid):
    """Deepest absolute level anywhere under ``nid`` (its own level if a leaf)."""
    kids = TREE[nid]["kids"]
    return max((_max_level(k) for k in kids), default=_level(nid))


class FakeSession:
    """Serves TREE with the real API's level semantics and failure modes."""

    def __init__(self, max_nodes=999):
        self.max_nodes = max_nodes      # bigger response than this -> 504
        self.calls = []

    def _collect(self, nid, to_level):
        node = TREE[nid]
        info = []
        for lv in range(_level(nid) + 1, _max_level(nid) + 1):
            info.append({"nodeLevel": lv, "count": 1, "expandLevel": 1, "enabled": True})
        out = {"nodeId": nid, "nodeTitle": node["title"], "level": _level(nid),
               "tocDescendantInfo": info, "nodes": [],
               "hasChildren": "yes" if node["kids"] else None,
               "anchorId": node.get("anchor"),
               "docFullPath": (DOC + node["urn"] + "-00000-00") if node.get("urn") else TOC}
        if _level(nid) < to_level:
            out["nodes"] = [self._collect(k, to_level) for k in node["kids"]]
        return out

    @staticmethod
    def _count(n):
        return 1 + sum(FakeSession._count(c) for c in n["nodes"])

    def request_json(self, url, body, *, method="POST", referer=None):
        nid, to_level = body["nodeId"], body["extractToLevel"]
        self.calls.append((nid, to_level))
        if to_level > _max_level(nid):
            raise ContentNotFound(
                f"HTTP 500 from {url} :: The input is not a valid Base-64 string")
        root = self._collect(nid, to_level)
        if self._count(root) > self.max_nodes:
            raise ContentNotFound(f"HTTP 504 from {url} :: Gateway Time-out")
        return {"tocEntity": {"tocContainer": {"tocNodes": [root]}}}


class FakeKernel:
    def __init__(self, session):
        self.session = session


def _crawler(max_nodes=999):
    toc = TocCrawler()
    toc.kernel = FakeKernel(FakeSession(max_nodes=max_nodes))
    return toc


def test_parses_string_haschildren_and_descendant_levels():
    """hasChildren is "yes"/null — bool() would call the string "no" a branch."""
    raw = {"tocEntity": {"tocContainer": {"tocNodes": [{
        "nodeId": "AAFAAD", "nodeTitle": "BANKRUPTCY RULES", "level": 2,
        "hasChildren": "yes",
        "tocDescendantInfo": [{"nodeLevel": 3, "count": 1}, {"nodeLevel": 4, "count": 2}],
        "docFullPath": TOC, "nodes": [],
    }]}}}
    n = nodes_from_toctree(raw)[0]
    assert n["has_children"] is True
    assert n["level"] == 2
    assert n["max_level"] == 4      # the only valid extractToLevel for this node

    raw["tocEntity"]["tocContainer"]["tocNodes"][0].update(
        hasChildren="no", tocDescendantInfo=[])
    assert nodes_from_toctree(raw)[0]["has_children"] is False


def test_single_request_when_the_whole_subtree_fits():
    toc = _crawler()
    nodes = toc.fetch_subtree(TOC, "AAF", _max_level("AAF"))
    assert toc.kernel.session.calls == [("AAF", 4)]
    assert {n["nodeid"] for n in nodes} == set(TREE)


def test_split_uses_each_branch_own_depth_not_the_titles():
    """The Cap 6 bug: every branch was asked for the *title's* depth (7), so any
    shallower branch 500'd and its whole subtree was lost."""
    toc = _crawler(max_nodes=4)              # whole title is 7 nodes -> 504
    logged = []
    nodes = toc.fetch_subtree(TOC, "AAF", _max_level("AAF"), on_progress=logged.append)

    assert {n["nodeid"] for n in nodes} == set(TREE), "a branch was lost"
    calls = dict(toc.kernel.session.calls)
    assert calls["AAFAAC"] == _max_level("AAFAAC") == 3   # not the title's 4
    assert calls["AAFAAD"] == _max_level("AAFAAD") == 4
    assert ("AAF", 2) in toc.kernel.session.calls         # children-only retry
    assert not any("WARNING" in m for m in logged)


def test_never_asks_deeper_than_a_node_can_go():
    """Every request must be a level the API would accept."""
    toc = _crawler(max_nodes=4)
    toc.fetch_subtree(TOC, "AAF", _max_level("AAF"))
    for nid, lvl in toc.kernel.session.calls:
        assert lvl <= _max_level(nid), f"over-deep request {nid}@{lvl} would 500"


def test_leaves_are_not_drilled_and_carry_their_urn():
    toc = _crawler(max_nodes=4)
    nodes = {n["nodeid"]: n for n in toc.fetch_subtree(TOC, "AAF", _max_level("AAF"))}
    assert nodes["AAFAAB"]["urn"] == "urn:contentItem:5XJ3-B-00000-00"
    assert nodes["AAFAAB"]["has_children"] is False
    assert "AAFAAB" not in [nid for nid, _ in toc.kernel.session.calls]


def test_sections_come_out_as_leaves_in_reading_order():
    toc = _crawler(max_nodes=4)
    secs = build_sections(toc.fetch_subtree(TOC, "AAF", _max_level("AAF")),
                          title="BANKRUPTCY ORDINANCE (CAP 6)")
    assert [s.nodeid for s in secs] == ["AAFAAB", "AAFAACAAB", "AAFAADAABAAB"]
    assert secs[2].hierarchy == ["BANKRUPTCY RULES (CAP 6A)", "Part I"]


def test_no_duplicate_requests_and_a_hard_cap():
    toc = _crawler(max_nodes=4)
    toc.fetch_subtree(TOC, "AAF", _max_level("AAF"))
    calls = toc.kernel.session.calls
    assert len(calls) == len(set(calls))
    assert len(calls) <= config.TOC_MAX_SUBTREE_REQUESTS


def test_anchored_branches_are_still_drilled_when_a_subtree_is_split():
    """An HK ordinance section carries an anchor *and* has annotation children.

    fetch_subtree used to skip any node with an anchor, so whenever the gateway
    forced a split the sections came back but every annotation under them was
    lost — silently except for the closing "stranded" warning.
    """
    tree = {
        "AAF":          {"title": "CAP 6", "kids": ["AAFAAB"]},
        "AAFAAB":       {"title": "PART I", "kids": ["AAFAABAAB"]},
        # the section: anchored AND a parent
        "AAFAABAAB":    {"title": "1. Short title", "kids": ["AAFAABAABAAB"],
                         "urn": "5XJ3-S1", "anchor": "AOHK.CAP6.S1"},
        "AAFAABAABAAB": {"title": "Enactment history", "kids": [], "urn": "5XJ3-S1",
                         "anchor": "AOHK.CAP6.S1.COMNTRY_1-01"},
    }
    original, TREE_REF = dict(TREE), TREE
    TREE_REF.clear()
    TREE_REF.update(tree)
    try:
        toc = _crawler(max_nodes=2)     # forces a split at every level
        logged = []
        nodes = toc.fetch_subtree(TOC, "AAF", _max_level("AAF"),
                                  on_progress=logged.append)
        ids = {n["nodeid"] for n in nodes}
        assert "AAFAABAAB" in ids, "the section itself was lost"
        assert "AAFAABAABAAB" in ids, "the annotation under an anchored section was lost"
        assert not any("WARNING" in m for m in logged), logged
    finally:
        TREE_REF.clear()
        TREE_REF.update(original)


def test_root_failure_propagates():
    toc = _crawler(max_nodes=0)          # nothing succeeds, not even children-only
    try:
        toc.fetch_subtree(TOC, "AAF", _max_level("AAF"))
    except ContentNotFound:
        pass
    else:
        raise AssertionError("expected ContentNotFound")


def _run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        print("  ok ", name)
    print("\nALL OK")


if __name__ == "__main__":
    _run()
