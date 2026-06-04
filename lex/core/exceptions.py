"""Typed errors so the orchestrator can react precisely (pause vs skip vs stop)."""


class HalsburyError(Exception):
    """Base for all scraper errors."""


class SessionExpired(HalsburyError):
    """Auth is dead (login redirect, 401/403, or locally-detected JWT expiry).

    Orchestrator should pause and prompt the user to re-authenticate, then resume.
    """


class BlockedError(HalsburyError):
    """A bot-management / rate-limit challenge was detected. Stop immediately."""


class ContentNotFound(HalsburyError):
    """The content container never rendered (transient or a genuinely empty doc)."""


class ParseError(HalsburyError):
    """The page rendered but could not be parsed into structured Markdown."""
