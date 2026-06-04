"""Fetch one section's content HTML, retrying transient misses with backoff."""

from __future__ import annotations

import time

from .. import config
from ..core import urls
from ..core.exceptions import BlockedError, ContentNotFound, SessionExpired
from ..core.models import Section
from .base import Plugin


class SectionFetcher(Plugin):
    def url_for(self, section: Section) -> str:
        return section.source_url or urls.build_viewer_url(section.urn, section.nodeid)

    def fetch_html(self, section: Section) -> str:
        url = self.url_for(section)
        last: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            try:
                return self.session.fetch_rendered(url)
            except (SessionExpired, BlockedError):
                raise  # auth/block are the orchestrator's call, never retried here
            except Exception as exc:  # timeouts, empty renders, transient nav errors
                last = exc
                time.sleep(config.RETRY_BACKOFF_BASE * (2 ** attempt))
        raise last if last else ContentNotFound(url)
