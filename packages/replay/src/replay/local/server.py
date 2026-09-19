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

Reads never wait. A live run - start, fork or resume - takes one to two minutes
against a local model, so it runs on a thread of its own and the request answers
at once with the new run's id and status "running"; the UI opens it and polls
its log as effects are recorded. One lock admits one live run at a time: the
kernel is sequential inside a run, and a second live request is told which run
is going rather than queued behind it for minutes. Nothing else takes that lock,
and the SQLite store locks each of its own operations.
"""

from __future__ import annotations

import dataclasses
import json
import mimetypes
import pathlib
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ..api import API_PREFIX, dispatch
from ..api.app import http_event
from ..api.http import conflict, created, response
from ..projector.projector import project_run, rebuild_all


class LocalApp:
    # How long a launch waits for the new run's metadata before answering anyway.
    STARTUP_WAIT_S = 30.0

    def __init__(self, store, views, runner, static_dir: pathlib.Path | None = None) -> None:
        self.store, self.views = store, views
        self.runner = None if runner is None else dataclasses.replace(runner, launch=self._launch)
        self.static_dir = static_dir
        self._live = threading.Lock()  # live runs only; reads never take it
        self.live_run: str | None = None
        self._live_thread: threading.Thread | None = None
        rebuild_all(store, views)

    def api(self, method: str, path: str, query: dict[str, str], body: str | None) -> dict[str, Any]:
        result = dispatch(http_event(method, path, query=query, body=body),
                          store=self.store, views=self.views, runner=self.runner)
        if method == "POST" and result["statusCode"] < 300:
            written = json.loads(result["body"]).get("run_id")
            if written:
                project_run(self.store, self.views, written)
        return result

    def _exists(self, run_id: str) -> bool:
        return any(m.run_id == run_id for m in self.store.list_runs())

    def _launch(self, run_id: str, job, echo: dict[str, Any]) -> dict[str, Any]:
        """Start a live run on its own thread and answer as soon as the run exists."""
        if not self._live.acquire(blocking=False):
            return conflict(f"{self.live_run} is running live; one live run at a time. Wait for it to end.")
        self.live_run = run_id
        failure: list[BaseException] = []
        finished = threading.Event()

        def drive() -> None:
            try:
                job()
            except BaseException as exc:  # the kernel has marked the run failed; say why here
                failure.append(exc)
                traceback.print_exc(file=sys.stderr)
            finally:
                try:
                    if self._exists(run_id):
                        project_run(self.store, self.views, run_id)
                finally:
                    self.live_run = None
                    self._live.release()
                    finished.set()

        self._live_thread = threading.Thread(target=drive, name=f"live-{run_id}", daemon=True)
        self._live_thread.start()
        deadline = time.monotonic() + self.STARTUP_WAIT_S
        while not finished.is_set() and not self._exists(run_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._exists(run_id):
            detail = f": {failure[0]}" if failure else ""
            return response(500, {"error": f"{run_id} did not start{detail}"})
        return created({**echo, "run_id": run_id, "created": True,
                        "status": self.store.get_metadata(run_id).status.value})

    def wait_for_live_run(self, timeout: float | None = None) -> bool:
        """For tests and shutdown: block until the live run, if any, has ended."""
        thread = self._live_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

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
