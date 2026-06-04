"""Orchestrator: harvest a publication/title's TOC, then fetch/parse/write each
section. Resumable, session-expiry aware, stop-on-block, and skip-and-log on
failure.

A ``RunControls`` object provides the hooks the per-section loop calls — progress
events, pause, stop, and re-authentication. The default implementation prints to
stdout and re-auths via the terminal (the CLI); the web UI supplies a subclass
that drives the browser UI and a pause/stop flag instead.
"""

from __future__ import annotations

from .. import config
from ..core.exceptions import BlockedError, SessionExpired
from ..core.manifest import Manifest
from .base import Plugin


class RunControls:
    """Default control hooks (CLI behaviour)."""

    def __init__(self, session=None):
        self.session = session

    def stopped(self) -> bool:
        return False

    def wait_if_paused(self) -> None:
        pass

    def reauth(self) -> None:
        if self.session is not None:
            self.session.wait_for_login()

    def event(self, kind: str, **data) -> None:
        if kind == "section":
            print(f"  [{data['i']}/{data['total']}] ✓ {data['label'][:64]}")
        elif kind == "fail":
            print(f"  ✗ {data.get('label', '')}: {data.get('error', '')[:90]}")
        elif kind == "info":
            print(data.get("msg", ""))


class Runner(Plugin):
    def __init__(self, manifest: Manifest | None = None):
        self._manifest = manifest

    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            self._manifest = Manifest()
        return self._manifest

    # --- entry points -----------------------------------------------------

    def run_title(self, seed_url: str, title: str | None = None,
                  volume: str | None = None, only_failed: bool = False,
                  controls: RunControls | None = None) -> dict:
        controls = controls or RunControls(self.session)
        if self.session is not None and not self.session.is_authed():
            controls.reauth()
        controls.event("info", msg=f"[toc] harvesting {title or 'all titles'} …")
        sections = self._harvest(seed_url, title, volume, controls)
        controls.event("info", msg=f"[toc] harvested {len(sections)} sections")
        return self.scrape_sections(sections, controls, only_failed=only_failed)

    def run_seeds(self, seeds, only_failed: bool = False,
                  controls: RunControls | None = None) -> list[dict]:
        return [self.run_title(url, title, volume, only_failed=only_failed,
                               controls=controls)
                for title, url, volume in seeds]

    def _harvest(self, seed_url, title, volume, controls):
        toc = self.kernel["toc"]
        try:
            return toc.harvest(seed_url, title, volume=volume)
        except SessionExpired:
            controls.reauth()
            return toc.harvest(seed_url, title, volume=volume)

    # --- the per-section loop (shared by CLI and UI) ----------------------

    def scrape_sections(self, sections, controls: RunControls,
                        only_failed: bool = False) -> dict:
        content = self.kernel["content"]
        parser = self.kernel["parser"]
        writer = self.kernel["writer"]
        for s in sections:
            self.manifest.record_harvested(s)

        failed_set = self.manifest.failed_urns() if only_failed else None
        done = skipped = failed = 0
        total = len(sections)
        for i, s in enumerate(sections, 1):
            controls.wait_if_paused()
            if controls.stopped():
                controls.event("info", msg="stopped")
                break
            if self.manifest.is_done(s.urn):
                skipped += 1
                continue
            if failed_set is not None and s.urn not in failed_set:
                skipped += 1
                continue
            label = f"{s.number}. {s.heading}"
            try:
                self._extract_one(s, content, parser, writer)
                done += 1
                controls.event("section", i=i, total=total, done=done, failed=failed,
                               title=s.title, urn=s.urn, label=label)
            except SessionExpired as exc:
                controls.event("info", msg=f"session expired ({exc}); re-authenticating")
                controls.reauth()
                try:
                    self._extract_one(s, content, parser, writer)
                    done += 1
                    controls.event("section", i=i, total=total, done=done,
                                   failed=failed, title=s.title, urn=s.urn, label=label)
                except Exception as exc2:  # noqa: BLE001
                    self._fail(s, exc2)
                    failed += 1
                    controls.event("fail", title=s.title, urn=s.urn, label=label,
                                   error=str(exc2))
            except BlockedError as exc:
                self._fail(s, exc)
                failed += 1
                controls.event("fail", title=s.title, urn=s.urn, label=label,
                               error=f"BLOCKED: {exc}")
                if config.STOP_ON_BLOCK:
                    controls.event("info", msg="stopping immediately (stop-on-block)")
                    break
            except Exception as exc:  # noqa: BLE001 — one bad section never kills the run
                self._fail(s, exc)
                failed += 1
                controls.event("fail", title=s.title, urn=s.urn, label=label,
                               error=f"{type(exc).__name__}: {exc}")

        stats = writer.compile_jsonl(self.manifest.done_records())
        controls.event("done", done=done, skipped=skipped, failed=failed,
                       total=total, records=stats.get("records", 0))
        return {"total": total, "done": done, "skipped": skipped, "failed": failed}

    def _extract_one(self, section, content, parser, writer) -> None:
        html = content.fetch_html(section)
        parsed = parser.parse_structured(
            html, section_number=section.number, heading=section.heading
        )
        self.manifest.mark_done(section.urn, writer.to_record(section, parsed))

    def _fail(self, section, exc) -> None:
        self.manifest.mark_failed(section.urn, repr(exc))
        try:
            config.FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(config.FAILURES_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"{section.urn}\t{section.title}\t{section.number}\t"
                         f"{type(exc).__name__}: {str(exc)[:200]}\n")
        except OSError:
            pass
