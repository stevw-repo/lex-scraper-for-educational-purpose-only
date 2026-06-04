"""Encode/decode Lexis+ ``pdtocnodeidentifier`` values.

A node identifier is a string of 3-character chunks. Each chunk is a base-26
number (A=0 .. Z=25)::

    seg = c0*676 + c1*26 + c2

Read left to right, one chunk per TOC level; each chunk is the (1-based) position
of a node among its siblings. Example::

    "AABAACAABAAB" -> [1, 2, 1, 1]

Use the decoded path ONLY for hierarchy reconstruction, stable sorting, and
dedupe — never to *synthesise* contentItem URNs (restricted alphabets and
per-title gaps make that unsafe; harvest URNs from the TOC instead).

Two cautions:
  * Unnumbered front-matter nodes (e.g. "Consultant Editor") still occupy a
    sibling position, so "Part 1" can decode to position 2.
  * The leftmost (title) chunk is relative to whatever root you loaded; read it
    from the entry point rather than hard-coding it.
"""

from __future__ import annotations

_A = ord("A")
_BASE = 26


def seg_to_int(seg: str) -> int:
    """Decode a single 3-char chunk to its integer position."""
    if len(seg) != 3 or not (seg.isascii() and seg.isalpha() and seg.isupper()):
        raise ValueError(f"invalid node-id segment: {seg!r}")
    return (ord(seg[0]) - _A) * 676 + (ord(seg[1]) - _A) * 26 + (ord(seg[2]) - _A)


def int_to_seg(n: int) -> str:
    """Encode an integer (0 <= n < 26**3) back to a 3-char chunk."""
    if not 0 <= n < _BASE ** 3:
        raise ValueError(f"segment value out of range: {n}")
    return "".join(chr(_A + (n // d) % _BASE) for d in (676, 26, 1))


def nodeid_to_path(nodeid: str) -> list[int]:
    """Decode a full node id into a list of per-level sibling positions."""
    if not nodeid or len(nodeid) % 3 != 0:
        raise ValueError(f"node id length not a multiple of 3: {nodeid!r}")
    return [seg_to_int(nodeid[i:i + 3]) for i in range(0, len(nodeid), 3)]


def path_to_nodeid(path: list[int]) -> str:
    """Encode a list of sibling positions back into a node id."""
    return "".join(int_to_seg(x) for x in path)


def sort_key(nodeid: str) -> list[int]:
    """Hierarchical sort key: documents sort into reading order by this key."""
    return nodeid_to_path(nodeid)


def depth(nodeid: str) -> int:
    """Number of tree levels in a node id."""
    return len(nodeid) // 3


def parent(nodeid: str) -> str | None:
    """The node id of the parent (one level up), or None for a root node."""
    return nodeid[:-3] if depth(nodeid) > 1 else None


def is_descendant(child: str, ancestor: str) -> bool:
    """True if ``child`` sits anywhere beneath ``ancestor`` in the tree."""
    return child != ancestor and child.startswith(ancestor) and len(child) % 3 == 0
