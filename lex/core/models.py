"""Data model for one harvested section (a TOC leaf)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import decode


@dataclass
class Section:
    """A single Halsbury's section/paragraph harvested from the TOC.

    ``hierarchy`` holds the intermediate node labels between the title and this
    section (Part, Group, Sub-group), in order — e.g.::

        ["1. Nature and Formation", "(1) The Relation of Agency"]
    """

    urn: str                       # contentItem URN (harvested, never synthesised)
    nodeid: str                    # pdtocnodeidentifier
    title: str                     # Halsbury title, e.g. "Agency"
    number: str                    # section number, e.g. "1" or "301"
    heading: str                   # section heading text
    hierarchy: list[str] = field(default_factory=list)
    volume: str | None = None      # e.g. "Volume 1 (2022)"
    source_url: str | None = None
    publication: str | None = None  # e.g. "Halsbury's Laws of England" (TOC root)

    @property
    def decoded_path(self) -> list[int]:
        """Per-level sibling positions; drives folder/section ordering."""
        return decode.nodeid_to_path(self.nodeid)

    @property
    def key(self) -> str:
        """Stable dedupe key."""
        return self.urn
