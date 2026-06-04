"""LexisSession — the transport kernel.

Owns a single persistent Playwright (Chromium) context so the real browser
carries the EZproxy/SSO session and its own cookies (including any bot-management
cookies — unlike the curl_cffi path in oreilly-ingest, we do NOT strip them here).
Provides paced ``fetch_rendered`` plus auth/block detection.

Playwright is imported lazily inside ``start()`` so the rest of the package
(decode, parser, writer, urls, manifest) imports without Playwright installed.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid

from .. import config
from . import jwt
from .exceptions import BlockedError, ContentNotFound, SessionExpired


class LexisSession:
    def __init__(self, headless: bool = False, profile_dir=None):
        self.headless = headless
        self.profile_dir = profile_dir or config.PROFILE_DIR
        self._pw = None
        self._context = None
        self._page = None
        self._last_request = 0.0

    # --- lifecycle --------------------------------------------------------

    def start(self) -> "LexisSession":
        if self._context is not None:
            return self
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._context = self._launch_context()
        except Exception as exc:
            if self._is_profile_locked_error(exc) and self._clear_stale_singleton_lock():
                self._context = self._launch_context()  # stale lock cleared -> retry once
            else:
                self._pw.stop()
                self._pw = None
                raise RuntimeError(self._locked_message()) from exc
        self._context.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        return self

    def _launch_context(self):
        return self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )

    @staticmethod
    def _is_profile_locked_error(exc: Exception) -> bool:
        s = str(exc).lower()
        return any(k in s for k in ("already in use", "existing browser session", "singleton"))

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _clear_stale_singleton_lock(self) -> bool:
        """Remove Chromium's Singleton* lock files iff the owning pid is dead.
        Returns True if a stale lock was cleared (safe to retry the launch)."""
        lock = self.profile_dir / "SingletonLock"
        try:
            owner_pid = int(os.readlink(lock).rsplit("-", 1)[-1])
        except (OSError, ValueError):
            owner_pid = None
        if owner_pid is not None and self._pid_alive(owner_pid):
            return False  # a live instance holds the profile — don't touch it
        cleared = False
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (self.profile_dir / name).unlink()
                cleared = True
            except OSError:
                pass
        return cleared

    def _locked_message(self) -> str:
        return (
            f"Browser profile {self.profile_dir} is in use by another live Chromium "
            "instance. Close any open 'Google Chrome for Testing' window, or run "
            "`pkill -f 'code/.state/profile'`, then retry."
        )

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._context = self._pw = self._page = None

    @property
    def page(self):
        if self._page is None:
            self.start()
        return self._page

    # --- pacing -----------------------------------------------------------

    def _throttle(self) -> None:
        wait = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
        elapsed = time.time() - self._last_request
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request = time.time()

    def _goto(self, url: str, wait_until: str = "domcontentloaded"):
        """Navigate and swallow nav timeouts. We wait on DOMContentLoaded (not the
        full 'load', which a heavy Lexis page may never reach due to images/ads/
        analytics); callers verify readiness via a selector or the URL afterwards."""
        page = self.page
        try:
            page.goto(url, wait_until=wait_until, timeout=config.NAV_TIMEOUT_MS)
        except Exception:
            pass
        return page

    @staticmethod
    def _safe_title(page) -> str:
        try:
            return page.title()
        except Exception:
            return ""

    # --- auth -------------------------------------------------------------

    def cookies(self) -> dict[str, str]:
        if self._context is None:
            return {}
        return {c["name"]: c["value"] for c in self._context.cookies()}

    def session_status(self) -> dict:
        """Cheap local check: decode any JWT cookie's ``exp``. ``valid`` is None
        when no JWT is present (caller should fall back to a navigation check)."""
        st = jwt.first_valid_token_status(self.cookies())
        return st or {"valid": None, "reason": "no_jwt", "expires_at": None}

    @staticmethod
    def _looks_like_login(url: str, title: str) -> bool:
        u = (url or "").lower()
        t = (title or "").lower()
        return any(h in u for h in config.LOGIN_URL_HINTS) or "sign in" in t

    def _authed_page(self):
        """Any open page that's on the authenticated Lexis host (not an SSO/login
        host). Scanning all pages handles SSO flows that finish in a new tab."""
        if self._context is None:
            return None
        for pg in self._context.pages:
            low = (pg.url or "").lower()
            if config.PROXY_HOST in low and not any(
                h in low for h in config.LOGIN_URL_HINTS
            ):
                return pg
        return None

    def _on_lexis_app(self) -> bool:
        """True (passively) when some open page is on the authenticated Lexis app.
        Adopts that page as the active one. Never navigates, so polling it can't
        interrupt a login the user is typing into."""
        pg = self._authed_page()
        if pg is not None:
            self._page = pg
            return True
        return False

    def is_logged_in(self) -> bool:
        """One-shot validation: navigate to the app once and see if we stay on the
        authenticated host. Do NOT call this in a polling loop (it navigates)."""
        self._throttle()
        self._goto(config.LEXIS_ENTRY_PROXIED)  # identity-profile entry, not /apac/
        return self._on_lexis_app()

    def is_authed(self) -> bool:
        """Passive check (no navigation): True if an open page is already on the
        authenticated Lexis host. Safe to call frequently or from a loop."""
        return self._on_lexis_app()

    def begin_login(self) -> None:
        """Start the browser and kick off the EZproxy/identity-profile login once
        (no waiting). Shared by the CLI wait loop and the web UI."""
        self.start()
        url = (self.page.url or "").lower()
        mid_sso = bool(url) and url != "about:blank" and config.PROXY_HOST not in url
        if not self._on_lexis_app() and not mid_sso:
            self._goto(config.EPROXY_LOGIN_URL)

    def wait_for_login(self, poll_seconds: int = 2) -> None:
        """Open the app once to trigger SSO, then wait passively for the user to
        finish. Does not re-navigate while waiting."""
        self.begin_login()
        print(
            "\n>>> A browser window is open. Log in to Lexis+ through HKU EZproxy "
            "(SSO / 2FA as needed).\n>>> I'll continue automatically once you're in — "
            "or just press Enter here to continue now.  (Ctrl+C to cancel.)"
        )
        signal = {"enter": False}

        def _watch_enter() -> None:
            try:
                input()
                signal["enter"] = True
            except (EOFError, RuntimeError):
                pass  # no stdin (non-interactive) — rely on auto-detection

        threading.Thread(target=_watch_enter, daemon=True).start()
        waited = 0
        while not self._on_lexis_app() and not signal["enter"]:
            time.sleep(poll_seconds)
            waited += poll_seconds
            if waited % 20 == 0:
                cur = ((self._page.url if self._page else "") or "")[:70]
                print(f"    …still waiting ({waited}s). Current page: {cur}  "
                      f"(or press Enter to continue)")
        print(">>> Login detected. Continuing.\n" if self._on_lexis_app()
              else ">>> Continuing on your signal.\n")

    # --- fetch ------------------------------------------------------------

    def fetch_rendered(self, url: str) -> str:
        """Navigate to a viewer URL and return the content-container outerHTML.

        Raises SessionExpired / BlockedError / ContentNotFound so the orchestrator
        can pause / stop / retry appropriately.
        """
        self._throttle()
        page = self._goto(url)  # domcontentloaded; nav timeout swallowed
        try:
            page.wait_for_selector(
                config.SEL_CONTENT_CONTAINER, timeout=config.CONTENT_WAIT_TIMEOUT_MS
            )
        except Exception:
            if self._looks_like_login(page.url, self._safe_title(page)):
                raise SessionExpired(f"redirected to login while fetching {url}")
            text = (page.content() or "").lower()
            if any(h in text for h in config.BLOCK_TEXT_HINTS):
                raise BlockedError("anti-bot / challenge page detected")
            raise ContentNotFound(f"content container did not render: {url}")

        el = page.query_selector(config.SEL_CONTENT_CONTAINER)
        return el.evaluate("e => e.outerHTML") if el else page.content()

    def get_page_html(self, url: str) -> str:
        """Navigate to a page and return its full HTML — for pages whose data is
        embedded in the served markup (e.g. the TOC page's `page.model`). No
        content-container wait."""
        self._throttle()
        page = self._goto(url)
        if self._looks_like_login(page.url, self._safe_title(page)):
            raise SessionExpired(f"redirected to login while loading {url}")
        return page.content()

    def request_json(self, url: str, body: dict, *, method: str = "POST",
                     referer: str | None = None) -> dict:
        """Authenticated JSON API call via the browser context (reuses the
        logged-in cookies). Returns parsed JSON; raises on auth/HTTP errors."""
        self.start()
        self._throttle()
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/plain, */*",
            "origin": config.BASE_URL,
            "x-ln-currentrequestid": str(uuid.uuid4()),
        }
        if referer:
            headers["referer"] = referer
        resp = self._context.request.fetch(
            url, method=method, data=json.dumps(body), headers=headers,
            timeout=config.NAV_TIMEOUT_MS,
        )
        if resp.status in (401, 403):
            raise SessionExpired(f"HTTP {resp.status} from {url}")
        if not resp.ok:
            raise ContentNotFound(f"HTTP {resp.status} from {url}")
        return resp.json()
