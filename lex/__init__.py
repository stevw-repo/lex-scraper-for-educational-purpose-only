"""Halsbury's Laws of England — Lexis+ HK extraction scraper.

Microkernel/plugin architecture (adapted from the oreilly-ingest reference):
a single transport kernel owns the authenticated Playwright session, pacing,
and auth-error detection; thin Lexis-specific plugins sit on top.
"""

__version__ = "0.1.0"
