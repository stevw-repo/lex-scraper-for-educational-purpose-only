"""Microkernel: owns the single shared session and a plugin registry.

Adapted from oreilly-ingest ``core/kernel.py``. The session is created lazily so
offline components (decode, parser, writer) can be imported and tested without
Playwright installed.
"""

from __future__ import annotations


class Kernel:
    def __init__(self, session=None):
        self._session = session
        self._plugins: dict[str, object] = {}

    @property
    def session(self):
        """The one shared LexisSession; built on first use (lazy Playwright import)."""
        if self._session is None:
            from .session import LexisSession
            self._session = LexisSession()
        return self._session

    def register(self, name: str, plugin) -> "Kernel":
        plugin.kernel = self
        self._plugins[name] = plugin
        return self

    def get(self, name: str):
        return self._plugins.get(name)

    def __getitem__(self, name: str):
        return self._plugins[name]

    def close(self):
        if self._session is not None:
            self._session.close()


def create_default_kernel(session=None) -> Kernel:
    """Register the standard Lexis plugins. Imported lazily to keep optional
    dependencies (Playwright, bs4) out of the import path for offline tools."""
    from ..plugins.auth import AuthPlugin
    from ..plugins.toc import TocCrawler
    from ..plugins.content import SectionFetcher
    from ..plugins.parser import SectionParser
    from ..plugins.writer import Output
    from ..plugins.runner import Runner

    kernel = Kernel(session=session)
    kernel.register("auth", AuthPlugin())
    kernel.register("toc", TocCrawler())
    kernel.register("content", SectionFetcher())
    kernel.register("parser", SectionParser())
    kernel.register("writer", Output())
    kernel.register("runner", Runner())
    return kernel
