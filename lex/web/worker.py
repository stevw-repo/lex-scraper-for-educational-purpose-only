"""Background worker that owns the Playwright session and runs scrape jobs.

Sync Playwright must live on a single thread, so ALL browser work happens here.
HTTP handlers never touch the session — they only enqueue commands / set flags
(thread-safe) and read :meth:`ScrapeWorker.snapshot`.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque

from .. import config
from ..core import urls
from ..core.exceptions import SessionExpired
from ..core.kernel import create_default_kernel
from ..core.session import LexisSession
from ..plugins.runner import RunControls
from ..plugins.toc import extract_to_level, split_title_volume

_BUSY = {"logging_in", "listing", "scraping", "paused", "stopping"}


class WorkerControls(RunControls):
    """Routes runner hooks to the worker (events → state/log; pause/stop → flags)."""

    def __init__(self, worker: "ScrapeWorker"):
        super().__init__(session=worker.session)
        self.w = worker

    def stopped(self) -> bool:
        return self.w._stop.is_set()

    def wait_if_paused(self) -> None:
        was_paused = False
        while self.w._pause.is_set() and not self.w._stop.is_set():
            if not was_paused:
                self.w._set(activity="paused")
                was_paused = True
            time.sleep(0.3)
        if was_paused and not self.w._stop.is_set():
            self.w._set(activity="scraping")

    def reauth(self) -> None:
        self.w._login_loop("session expired — log in again in the browser window")

    def event(self, kind: str, **data) -> None:
        self.w._on_event(kind, data)


class ScrapeWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.session: LexisSession | None = None
        self.kernel = None
        self.controls: WorkerControls | None = None
        self._q: queue.Queue = queue.Queue()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._login_confirm = threading.Event()
        self._shutdown = threading.Event()
        self._lock = threading.RLock()
        self._log: deque[str] = deque(maxlen=300)
        self._titles: list[dict] = []
        self._state = {
            "logged_in": False,
            "activity": "idle",
            "publication": None,
            "publication_url": None,
            "toc_fullpath": None,
            "current": "",
            "overall": {"done": 0, "failed": 0, "total": 0, "title": None},
            "pacing": {"min": config.REQUEST_DELAY_MIN, "max": config.REQUEST_DELAY_MAX},
        }

    # --- thread-safe API for the HTTP handlers ----------------------------

    def submit(self, cmd: str, **kw) -> None:
        self._q.put((cmd, kw))

    def busy(self) -> bool:
        with self._lock:
            return self._state["activity"] in _BUSY

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def confirm_login(self) -> None:
        self._login_confirm.set()

    def set_pacing(self, mn: float, mx: float) -> None:
        mn = max(0.0, float(mn))
        mx = max(mn, float(mx))
        config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX = mn, mx
        self._set(pacing={"min": mn, "max": mx})
        self._log_line(f"pacing set to {mn:g}–{mx:g}s")

    def snapshot(self) -> dict:
        with self._lock:
            st = {k: (dict(v) if isinstance(v, dict) else v)
                  for k, v in self._state.items()}
            st["titles"] = [dict(t) for t in self._titles]
            st["log"] = list(self._log)
            st["paused"] = self._pause.is_set()
            st["can_confirm_login"] = self._state["activity"] == "logging_in"
        return st

    # --- worker thread ----------------------------------------------------

    def run(self) -> None:
        self.session = LexisSession(headless=False)
        self.kernel = create_default_kernel(session=self.session)
        self.controls = WorkerControls(self)
        while not self._shutdown.is_set():
            try:
                cmd, kw = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._dispatch(cmd, kw)
            except Exception as exc:  # noqa: BLE001 — never let a job kill the worker
                self._log_line(f"error in {cmd}: {type(exc).__name__}: {exc}")
            finally:
                self._stop.clear()
                self._pause.clear()
                self._set(activity="idle", current="")

    def _dispatch(self, cmd: str, kw: dict) -> None:
        if cmd == "login":
            self._login_loop("log in to Lexis+ in the browser window")
        elif cmd == "publication":
            self._set_publication(kw["url"])
        elif cmd == "scrape_all":
            self._scrape_titles([t["nodeid"] for t in self._titles])
        elif cmd == "scrape_selected":
            self._scrape_titles(kw.get("nodeids") or [])
        elif cmd == "scrape_title":
            self._scrape_titles([kw["nodeid"]])
        elif cmd == "retry":
            self._do_retry(kw.get("nodeid"))
        elif cmd == "build":
            self._build()

    def _login_loop(self, reason: str) -> None:
        self._set(activity="logging_in")
        self._log_line(reason)
        self._login_confirm.clear()
        self.session.begin_login()
        authed = False
        while not self._shutdown.is_set():
            if self.session.is_authed():        # passive auto-detect
                authed = True
                break
            if self._login_confirm.is_set():    # user pressed "Continue"
                break
            time.sleep(1.0)
        self._login_confirm.clear()
        if not authed:                          # verify actively (navigates to the app)
            authed = self.session.is_logged_in()
        self._set(logged_in=authed, activity="idle")
        self._log_line("login confirmed" if authed
                       else "could not confirm login — please try Log in again")

    def _set_publication(self, url: str) -> None:
        if not self.session.is_authed():
            self._login_loop("log in to load the publication")
        self._set(activity="listing")
        self._log_line("loading publication TOC …")
        toc = self.kernel["toc"]
        toc_fullpath = urls.parse_toc_url(url).get("toc_fullpath") or config.ROOT_TOC_FULLPATH
        titles = toc.list_titles(toc_fullpath)
        with self._lock:
            self._state.update(publication=toc.publication, publication_url=url,
                               toc_fullpath=toc_fullpath)
            self._titles = [{
                "nodeid": t["nodeid"], "name": t["name"],
                "clean": split_title_volume(t["name"])[0],
                "countsbylevel": t["countsbylevel"],
                "done": 0, "failed": 0, "total": None, "status": "not started",
            } for t in titles]
        self._apply_manifest_counts()
        self._set(activity="idle")
        self._log_line(f"{len(titles)} titles in {toc.publication}")

    def _scrape_titles(self, nodeids: list[str], only_failed: bool = False) -> None:
        """Harvest + scrape each title in turn, so scraping starts immediately and
        Pause/Stop stay responsive (vs. harvesting all titles up front)."""
        if not nodeids:
            self._log_line("no titles selected")
            return
        if not self.session.is_authed():
            self._login_loop("log in to scrape")
        runner = self.kernel["runner"]
        self._set(activity="scraping")
        for nid in nodeids:
            if self.controls.stopped():
                break
            self.controls.wait_if_paused()
            if self.controls.stopped():
                break
            t = self._find_title(nid)
            if not t:
                continue
            self._set(activity="scraping", current=f"harvesting {t['clean']} …")
            self._log_line(f"▶ {t['clean']}")
            try:
                sections = self._harvest_title(t)
            except Exception as exc:  # noqa: BLE001
                self._log_line(f"harvest failed for {t['clean']}: {exc}")
                continue
            # known total the moment we harvest, so the row shows N (not "—") live
            with self._lock:
                t["total"] = len(sections)
                t["status"] = self._status_of(t["done"], t["failed"], len(sections))
            runner.scrape_sections(sections, self.controls, only_failed=only_failed)
            self._apply_manifest_counts()

    def _do_retry(self, nodeid: str | None) -> None:
        if nodeid:
            self._scrape_titles([nodeid], only_failed=True)
            return
        # retry-all: only the titles that currently have failures
        nids = [t["nodeid"] for t in self._titles if t.get("failed")]
        if not nids:
            self._log_line("no failures to retry")
            return
        self._scrape_titles(nids, only_failed=True)

    def _harvest_title(self, t: dict):
        toc = self.kernel["toc"]
        level = extract_to_level(t["countsbylevel"])
        args = (self._state["toc_fullpath"], t["nodeid"], t["name"], level)
        try:
            return toc.harvest_title(*args, publication=self._state["publication"])
        except SessionExpired:
            self.controls.reauth()
            return toc.harvest_title(*args, publication=self._state["publication"])

    def _build(self) -> None:
        runner = self.kernel["runner"]
        stats = self.kernel["writer"].compile_jsonl(runner.manifest.done_records())
        self._log_line(f"built corpus: {stats['records']} records, "
                       f"{stats['titles']} title(s)")

    # --- event handling + state -------------------------------------------

    def _on_event(self, kind: str, data: dict) -> None:
        if kind == "section":
            self._set(current=data["label"], overall={
                "done": data["done"], "failed": data["failed"],
                "total": data["total"], "title": data.get("title")})
            self._bump_title(data.get("title"), done_delta=1)
            self._log_line(f"✓ {data.get('title')}: {data['label'][:70]}")
        elif kind == "fail":
            self._bump_title(data.get("title"), failed_delta=1)
            self._log_line(f"✗ {data.get('title')}: {data.get('label', '')[:48]} — "
                           f"{data.get('error', '')[:80]}")
        elif kind == "info":
            self._log_line(data.get("msg", ""))
        elif kind == "done":
            self._log_line(f"finished: {data['done']} written, {data['failed']} "
                           f"failed, {data['skipped']} skipped")

    def _apply_manifest_counts(self) -> None:
        tc = self.kernel["runner"].manifest.title_counts()
        with self._lock:
            for t in self._titles:
                c = tc.get(t["clean"])
                if c:
                    t.update(done=c["done"], failed=c["failed"], total=c["total"],
                             status=self._status_of(c["done"], c["failed"], c["total"]))

    def _bump_title(self, clean: str | None, done_delta=0, failed_delta=0) -> None:
        with self._lock:
            for t in self._titles:
                if t["clean"] == clean:
                    t["done"] += done_delta
                    t["failed"] += failed_delta
                    t["status"] = self._status_of(t["done"], t["failed"], t["total"]) \
                        if t["total"] else "in progress"
                    break

    @staticmethod
    def _status_of(done: int, failed: int, total) -> str:
        if not total:
            return "in progress" if (done or failed) else "not started"
        if total - done - failed <= 0:
            return "complete" if failed == 0 else "has failures"
        return "in progress" if (done or failed) else "not started"

    def _find_title(self, nodeid: str) -> dict | None:
        with self._lock:
            return next((t for t in self._titles if t["nodeid"] == nodeid), None)

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def _log_line(self, msg: str) -> None:
        if not msg:
            return
        with self._lock:
            self._log.append(f"{time.strftime('%H:%M:%S')}  {msg}")
