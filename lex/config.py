"""Central configuration: hosts, pacing, paths, and all CSS selectors.

Keeping every Lexis selector here means SPA/DOM drift is a one-file edit.
"""

from pathlib import Path

# --- Paths ---
PKG_DIR = Path(__file__).resolve().parent          # the lex/ package
PROJECT_DIR = PKG_DIR.parent                        # project root
OUTPUT_DIR = PROJECT_DIR / "output"                 # the Markdown corpus
STATE_DIR = PROJECT_DIR / ".state"                  # operational state (not corpus)
PROFILE_DIR = STATE_DIR / "profile"                 # persistent Playwright user-data-dir
MANIFEST_PATH = STATE_DIR / "manifest.sqlite"
FAILURES_LOG = STATE_DIR / "failures.log"
SEEDS_FILE = PROJECT_DIR / "seeds.txt"              # one TOC/landing URL per title

# --- Hosts (HKU EZproxy rewrite of plus.lexis.com) ---
PROXY_HOST = "plus-lexis-com.eproxy.lib.hku.hk"
BASE_URL = f"https://{PROXY_HOST}"
PDMFID = "1539266"                                  # institution config id (from sample URLs)
CONTENT_SET = "/shared/document/analytical-materials-uk/"  # constant pddocfullpath prefix

# Institutional identity profile — THE key to logging in headlessly. Navigating
# to /apac/ directly makes Lexis show its own sign-in page; entering via
# /hk?identityprofileid=<ID> makes Lexis auto-provision the session from HKU's
# subscription instead. Find <ID> as the `identityprofileid` query param when you
# open Lexis+ HK via the HKU Library database link (here: from the working login).
IDENTITY_PROFILE_ID = "SS4FV457426"
# Proxied entry (use when the EZproxy session is already alive):
LEXIS_ENTRY_PROXIED = f"{BASE_URL}/hk?identityprofileid={IDENTITY_PROFILE_ID}"
# Full EZproxy-login wrapper (use for the initial/interactive login — also drives
# HKU SSO if needed). Kept un-encoded to match the URL the library actually uses.
EPROXY_LOGIN_URL = (
    "https://eproxy.lib.hku.hk/login?url="
    f"https://plus.lexis.com/hk?identityprofileid={IDENTITY_PROFILE_ID}"
)

# --- TOC data API (replicated from the SPA; see expand_log net-export) -------
# The whole-work TOC root; each of the ~148 titles is a child node (Agency=AAB).
ROOT_TOC_FULLPATH = "/shared/tableofcontents/urn:contentItem:5M8K-C9S1-FBXB-D000-00000-00"
# POST {action,tocId(base64 of pdtocfullpath),nodeId,extractToLevel,masterFeatureContext}
# returns the node's whole subtree as JSON (tocEntity.tocContainer.tocNodes[]).
TOCTREE_ENDPOINT = f"{BASE_URL}/apac/f/TocInfo/toctreeresults"
TOC_MAX_LEVEL = 12   # fallback extractToLevel when a title's countsbylevel is unknown

# --- Pacing (moderate, jittered, serial — per confirmed decision) ---
REQUEST_DELAY_MIN = 2.0
REQUEST_DELAY_MAX = 4.0
NAV_TIMEOUT_MS = 45_000
CONTENT_WAIT_TIMEOUT_MS = 30_000
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 3.0                            # seconds; * 2**attempt

# --- Safeguards (off by default per "moderate, no cap"; stop-on-block always on) ---
MAX_PER_DAY: int | None = None
MAX_TOTAL: int | None = None
STOP_ON_BLOCK = True

# --- Optional HTTP fast-path (Playwright is the default + correctness baseline) ---
USE_HTTP_FASTPATH = False

# --- Selectors (the real Lexis SS_ markup, verified against saved samples) ---
SEL_CONTENT_CONTAINER = (
    ".SS_contentdocument, .document-content-container, .section-content-container"
)
SEL_DOC_TITLE = "ln-document-title, .documentTitle"
SEL_HEADINGS = "h1.SS_Heading1, h2.SS_Heading2, h3.SS_Heading3"
SEL_FOOTNOTE_REF = "a.SS_FootnoteReference"
SEL_FOOTNOTE_FOOTER = "footer.SS_Footnote"
SEL_FOOTNOTE_DEF_NUM = ".SS_FootnoteDefinition_Content"
SEL_FOOTNOTE_BODY = ".SS_FootnoteBody"
SEL_EMBEDDED_LINK = "a.SS_EmbeddedLink"
SEL_DRAFTING_NOTE = ".SS_DraftingNote"
# Login / block detection hints (substring match on URL).
# A page on one of these hosts/paths means we are NOT authenticated yet.
LOGIN_URL_HINTS = (
    "signin-lexisnexis", "/lnaccess/", "lnaccess/app/signin",  # Lexis own sign-in
    "ids.hku.hk", "/idp/", "shibboleth",                       # HKU SSO
    "eproxy.lib.hku.hk/login", "/connect?session",             # EZproxy login wrapper
    "openathens",
)
BLOCK_TEXT_HINTS = ("unusual activity", "access denied", "are you a robot", "request blocked")

# --- Parser behaviour (minor open items, with confirmed defaults) ---
KEEP_DRAFTING_NOTES = True       # render SS_DraftingNote as a blockquote
RELINK_CROSSREFS = False         # keep SS_EmbeddedLink text only (no local relinking yet)
EMIT_PROVENANCE_COMMENT = False  # no front-matter; provenance lives in the manifest
MD_HEADING_STYLE = "ATX"         # markdownify heading style

# --- Output records ---
JURISDICTION = "England & Wales"   # Halsbury's Laws of England

# --- TOC crawling -----------------------------------------------------------
# NOTE: these selectors are best-effort and MUST be confirmed against the live
# TOC DOM on first run (we have links.txt/TOC.txt but no saved TOC HTML). The
# hierarchy-reconstruction logic in toc.build_sections() is DOM-independent and
# unit-tested; only the extraction selectors below are expected to need tuning.
SEL_TOC_READY = "[class*='toc'], ln-toc, ln-apacnavigationmfe"  # tree-present signal
SEL_TOC_EXPANDABLE = "[aria-expanded='false']"                  # collapsed nodes to open
SEL_TOC_NODE = "a[href*='pddocfullpath'], [data-pdtocnodeidentifier]"
TOC_NODEID_ATTR = "data-pdtocnodeidentifier"   # attr carrying a node's id (structural nodes)
TOC_MAX_EXPAND_PASSES = 80
TOC_EXPAND_SETTLE_MS = 400
