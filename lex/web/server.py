"""Local web server for the scraper UI.

Stdlib ``http.server`` only. The frontend polls ``GET /api/state`` ~1×/s and
POSTs commands; the heavy lifting runs on the shared :class:`ScrapeWorker`.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .worker import ScrapeWorker

_STATIC = Path(__file__).resolve().parent / "static"
_TYPES = {".html": "text/html; charset=utf-8",
          ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8"}
# Job-STARTING endpoints, rejected while a job is running. Note: /api/login/confirm,
# pause, resume, stop, pacing are deliberately NOT here — they must work mid-job
# (esp. confirm, which ENDS the logging_in state).
_GUARDED = {"/api/login", "/api/publication", "/api/scrape", "/api/retry", "/api/build"}


class Handler(BaseHTTPRequestHandler):
    worker: ScrapeWorker = None  # set in serve()

    def log_message(self, *args):  # quiet the default access log
        pass

    # --- helpers ----------------------------------------------------------

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            return {}

    def _static(self, name: str) -> None:
        path = (_STATIC / name).resolve()
        if not str(path).startswith(str(_STATIC)) or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._static("index.html")
        elif self.path in ("/app.js", "/style.css"):
            self._static(self.path.lstrip("/"))
        elif self.path == "/api/state":
            self._json(self.worker.snapshot())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path
        if path in _GUARDED and self.worker.busy():
            self._json({"error": "busy", "activity": self.worker.snapshot()["activity"]}, 409)
            return

        body = self._body()
        if path == "/api/login":
            self.worker.submit("login")
        elif path == "/api/login/confirm":
            self.worker.confirm_login()
        elif path == "/api/publication":
            url = (body.get("url") or "").strip()
            if not url:
                return self._json({"error": "url required"}, 400)
            self.worker.submit("publication", url=url)
        elif path == "/api/scrape":
            scope = body.get("scope")
            if scope == "title" and body.get("nodeid"):
                self.worker.submit("scrape_title", nodeid=body["nodeid"])
            elif scope == "selected":
                self.worker.submit("scrape_selected", nodeids=body.get("nodeids") or [])
            else:
                self.worker.submit("scrape_all")
        elif path == "/api/retry":
            self.worker.submit("retry", nodeid=body.get("nodeid"))
        elif path == "/api/build":
            self.worker.submit("build")
        elif path == "/api/pause":
            self.worker.pause()
        elif path == "/api/resume":
            self.worker.resume()
        elif path == "/api/stop":
            self.worker.stop()
        elif path == "/api/pacing":
            self.worker.set_pacing(body.get("min", 2.0), body.get("max", 4.0))
        else:
            return self._json({"error": "not found"}, 404)
        self._json({"ok": True})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    worker = ScrapeWorker()
    worker.start()
    Handler.worker = worker
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"\n  Lex scraper UI → {url}\n  (Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down …")
    finally:
        httpd.server_close()
        if worker.session is not None:
            worker.session.close()
