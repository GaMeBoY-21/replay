"""The local server: the whole product on localhost, no AWS.

    uv run python -m replay.local --db replay.local.db --seed fixtures/canonical

It contains no application logic. An HTTP request becomes the API Gateway event
a Lambda would receive, goes through the same `dispatch` the Lambda calls, and
the response goes back unchanged. The shape matches the deployed one: the app
under `/`, the API under `/api/*`, one origin, so the frontend needs no API URL
and nothing needs CORS.

Deployed, DynamoDB Streams drive the projector. Here there is no stream, so the
projection of a run is rebuilt straight after a request writes it. Same function,
different trigger.

Handlers are synchronous and drive the agent with `asyncio.run`, which cannot run
inside an event loop, so each request is served on its own thread - and a lock
makes them one at a time, as sequential as the kernel they call.
"""

from __future__ import annotations

import json
import mimetypes
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ..api import API_PREFIX, dispatch
from ..api.app import http_event
from ..projector.projector import project_run, rebuild_all


class LocalApp:
    def __init__(self, store, views, runner, static_dir: pathlib.Path | None = None) -> None:
        self.store, self.views, self.runner = store, views, runner
        self.static_dir = static_dir
        self._lock = threading.Lock()
        rebuild_all(store, views)

    def api(self, method: str, path: str, query: dict[str, str], body: str | None) -> dict[str, Any]:
        with self._lock:
            result = dispatch(http_event(method, path, query=query, body=body),
                              store=self.store, views=self.views, runner=self.runner)
            if method == "POST" and result["statusCode"] < 300:
                written = json.loads(result["body"]).get("run_id")
                if written:
                    project_run(self.store, self.views, written)
            return result

    def static(self, path: str) -> tuple[int, str, bytes]:
        if self.static_dir is None:
            return 404, "application/json", json.dumps({"error": "no frontend is being served"}).encode()
        root = self.static_dir.resolve()
        target = (root / path.lstrip("/")).resolve()
        if root not in target.parents and target != root:
            return 404, "application/json", b'{"error": "not found"}'
        if not target.is_file():
            target = root / "index.html"  # the SPA handles its own routes
        if not target.is_file():
            return 404, "application/json", b'{"error": "not found"}'
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return 200, kind, target.read_bytes()


def make_handler(app: LocalApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - quiet by default
            pass

        def _serve(self, method: str) -> None:
            url = urlsplit(self.path)
            if url.path == API_PREFIX or url.path.startswith(API_PREFIX + "/"):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length).decode() if length else None
                result = app.api(method, url.path, dict(parse_qsl(url.query)), body)
                payload = result["body"].encode()
                self.send_response(result["statusCode"])
                self.send_header("content-type", result["headers"]["content-type"])
            else:
                status, kind, payload = app.static(url.path)
                self.send_response(status)
                self.send_header("content-type", kind)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

    return Handler


def serve(app: LocalApp, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
