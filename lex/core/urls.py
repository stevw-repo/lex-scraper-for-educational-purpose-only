"""Build and parse Lexis+ document-viewer URLs.

Only ``pddocfullpath`` (the contentItem URN) and ``pdtocnodeidentifier`` identify
a section; ``crid`` / ``pdscrollreferenceid`` / ``pdisdocsliderrequired`` are UI
state and are dropped from URLs we build.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import config

_URN_RE = re.compile(r"(urn:contentItem:[A-Za-z0-9-]+)")


def build_viewer_url(
    urn: str,
    nodeid: str,
    *,
    pdmfid: str = config.PDMFID,
    content_set: str = config.CONTENT_SET,
    base_url: str = config.BASE_URL,
) -> str:
    """Assemble the minimal viewer URL for a harvested section."""
    if not urn.startswith("urn:"):
        urn = "urn:contentItem:" + urn
    pddocfullpath = content_set + urn
    query = (
        f"pdmfid={pdmfid}"
        f"&pddocfullpath={quote(pddocfullpath, safe=':')}"  # encode '/', keep ':'
        f"&pdtocnodeidentifier={nodeid}"
    )
    return f"{base_url}/apac/document/?{query}"


def parse_viewer_url(url: str) -> dict:
    """Extract ``urn`` / ``nodeid`` / ``pdmfid`` from a viewer URL or href."""
    q = parse_qs(urlparse(url).query)
    pdfp = unquote(q.get("pddocfullpath", [""])[0])
    m = _URN_RE.search(pdfp)
    return {
        "urn": m.group(1) if m else None,
        "nodeid": q.get("pdtocnodeidentifier", [None])[0],
        "pdmfid": q.get("pdmfid", [None])[0],
    }


def looks_like_section_href(href: str) -> bool:
    """True if an anchor points at a document/section (carries a contentItem URN)."""
    return "pddocfullpath" in href and "urn:contentItem:" in unquote(href)


# --- TOC table-of-contents URLs + the toctreeresults API ---------------------

def build_toc_url(
    toc_fullpath: str = config.ROOT_TOC_FULLPATH,
    *,
    pdmfid: str = config.PDMFID,
    base_url: str = config.BASE_URL,
) -> str:
    """The viewer URL for a table-of-contents path."""
    return (
        f"{base_url}/apac/toc?pdmfid={pdmfid}"
        f"&pdtocfullpath={quote(toc_fullpath, safe=':')}"
    )


def parse_toc_url(url: str) -> dict:
    """Extract ``toc_fullpath`` / ``pdmfid`` from a /apac/toc URL (or {} if none)."""
    q = parse_qs(urlparse(url).query)
    return {
        "toc_fullpath": unquote(q.get("pdtocfullpath", [""])[0]) or None,
        "pdmfid": q.get("pdmfid", [config.PDMFID])[0],
    }


def tocid_b64(toc_fullpath: str) -> str:
    """The ``tocId`` field for toctreeresults = base64 of the TOC full path."""
    return base64.b64encode(toc_fullpath.encode()).decode()
