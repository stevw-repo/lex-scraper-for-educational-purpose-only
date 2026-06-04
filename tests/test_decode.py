"""Tests for the pdtocnodeidentifier decoder.

Ground truth: the decode table in `Scraping approach.txt` plus the node ids
harvested in `TOC/links.txt` (Agency §1–§4, Agricultural §301–§304).

Runnable two ways:
    python tests/test_decode.py          # stdlib only, no pytest needed
    pytest tests/test_decode.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # code/ on path

from lex.core import decode  # noqa: E402

# nodeid -> expected decoded path (from approach.txt table + links.txt)
KNOWN = {
    "AABAACAABAAB": [1, 2, 1, 1],        # Agency §1  Nature of the relation of agency
    "AABAACAABAAC": [1, 2, 1, 2],        # Agency §2  Other uses of the word 'agent'
    "AABAACAACAABAAB": [1, 2, 2, 1, 1],  # Agency §3  General rule as to competency
    "AABAACAACAABAAC": [1, 2, 2, 1, 2],  # Agency §4  Competency of alien enemies
    "AACAACAABAAB": [2, 2, 1, 1],        # Agric  §301 Tenancies beginning on/after 1995
    "AACAACAABAAC": [2, 2, 1, 2],        # Agric  §302 Conditions for farm business tenancies
    "AACAACAABAAD": [2, 2, 1, 3],        # Agric  §303 Crown land
    "AACAACAACAAB": [2, 2, 2, 1],        # Agric  §304 Termination of the tenancy
}


def test_segment_round_trip():
    for n in [0, 1, 2, 25, 26, 27, 51, 676, 700, 17_575]:
        assert decode.seg_to_int(decode.int_to_seg(n)) == n


def test_known_nodeids_decode():
    for nid, path in KNOWN.items():
        assert decode.nodeid_to_path(nid) == path, nid


def test_known_nodeids_round_trip():
    for nid, path in KNOWN.items():
        assert decode.path_to_nodeid(path) == nid, nid


def test_reading_order_sort():
    # §301, §302, §303 share a group; §304 starts the next group — all under title 2
    ids = ["AACAACAACAAB", "AACAACAABAAD", "AACAACAABAAB", "AACAACAABAAC"]
    assert sorted(ids, key=decode.sort_key) == [
        "AACAACAABAAB", "AACAACAABAAC", "AACAACAABAAD", "AACAACAACAAB",
    ]


def test_hierarchy_helpers():
    assert decode.depth("AABAACAABAAB") == 4
    assert decode.parent("AABAACAABAAB") == "AABAACAAB"
    assert decode.parent("AAB") is None
    # §3 sits beneath the (i) Competency-of-Principals sub-group node
    assert decode.is_descendant("AABAACAACAABAAB", "AABAACAAC")
    assert not decode.is_descendant("AABAACAABAAB", "AAC")


def test_front_matter_offset_documented():
    # Part "1. Nature and Formation" decodes to sibling position 2 because the
    # unnumbered "Consultant Editor" front-matter node occupies position 1.
    assert decode.nodeid_to_path("AABAACAABAAB")[1] == 2


def test_invalid_inputs():
    for bad in ["", "AB", "ABCD", "abc", "12A", "A B"]:
        try:
            decode.nodeid_to_path(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} decode tests passed")


if __name__ == "__main__":
    _run()
