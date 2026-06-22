"""Fetch one section's content HTML, retrying transient misses with backoff."""

from __future__ import annotations

import time

from .. import config
from ..core import urls
from ..core.exceptions import BlockedError, ContentNotFound, SessionExpired
from ..core.models import Section
from .base import Plugin


class SectionFetcher(Plugin):
    def __init__(self):
        self._html_cache: dict[tuple[str | None, str], str] = {}

    def url_for(self, section: Section) -> str:
        return section.source_url or urls.build_viewer_url(
            section.urn, section.nodeid, scroll_reference_id=section.anchor_id
        )

    def cache_key_for_url(self, url: str) -> tuple[str | None, str]:
        parsed = urls.parse_viewer_url(url)
        return (parsed.get("pdmfid"), parsed.get("doc_fullpath") or url)

    def clear_cache(self) -> None:
        self._html_cache.clear()

    def fetch_html(self, section: Section) -> str:
        url = self.url_for(section)
        key = self.cache_key_for_url(url)
        if key in self._html_cache:
            return self._html_cache[key]

        last: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            try:
                html = self.session.fetch_rendered(url)
                self._remember(key, html)
                return html
            except (SessionExpired, BlockedError):
                raise  # auth/block are the orchestrator's call, never retried here
            except Exception as exc:  # timeouts, empty renders, transient nav errors
                last = exc
                time.sleep(config.RETRY_BACKOFF_BASE * (2 ** attempt))
        raise last if last else ContentNotFound(url)

    def _remember(self, key: tuple[str | None, str], html: str) -> None:
        self._html_cache[key] = html
        max_size = max(1, config.DOCUMENT_HTML_CACHE_SIZE)
        while len(self._html_cache) > max_size:
            self._html_cache.pop(next(iter(self._html_cache)))
