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

A live run can be cancelled: its breakers are handed a flag, and it stops at the
next effect exactly as a breaker halt stops it. And a run that was still marked
running when the server last stopped was interrupted - nothing is driving it any
more - so on start it gets a closing event saying so, and that status.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .. import site
from ..api import API_PREFIX, dispatch
from ..api.app import http_event
from replay_events import RunEnded, RunStatus

from ..api.http import conflict, created, response
from ..kernel import resolve
from ..kernel.context import RunContext
from ..projector.projector import project_run, rebuild_all

INTERRUPTED = "The server stopped while this run was live. Everything it recorded up to then is kept."


def end_interrupted_runs(store) -> list[str]:
    """Close every run still marked running: none can be, on a fresh start.

    The closing event goes through RunContext.append, the only path that stamps
    an eid, at the run's next eid - so the log is appended to, never rewritten.
    """
    ended = []
    for metadata in store.list_runs():
        if metadata.status != RunStatus.RUNNING:
            continue
        context = RunContext(metadata.run_id, store, eid_base=resolve(store, metadata.run_id).next_eid)
        context.append(RunEnded(status=RunStatus.INTERRUPTED.value, detail=INTERRUPTED))
        store.put_metadata(metadata.model_copy(update={"status": RunStatus.INTERRUPTED}))
        ended.append(metadata.run_id)
    return ended


class LocalApp:
    # How long a launch waits for the new run's metadata before answering anyway.
    STARTUP_WAIT_S = 30.0

    def __init__(self, store, views, runner, static_dir: pathlib.Path | None = None) -> None:
        self.store, self.views = store, views
        self.runner = (None if runner is None
                       else dataclasses.replace(runner, launch=self._launch, cancel=self.cancel))
        self.static_dir = static_dir
        self._live = threading.Lock()  # live runs only; reads never take it
        self.live_run: str | None = None
        self._live_thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self.interrupted = end_interrupted_runs(store)
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
        self._cancel = cancel = threading.Event()
        failure: list[BaseException] = []
        finished = threading.Event()

        def drive() -> None:
            try:
                job(cancel)
            except BaseException as exc:  # the kernel has marked the run failed; say why here
                failure.append(exc)
                traceback.print_exc(file=sys.stderr)
            finally:
                try:
                    if self._exists(run_id):
                        project_run(self.store, self.views, run_id)
                finally:
                    self.live_run = None
                    self._cancel = None
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

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Ask the live run to stop at its next effect. It ends as a halt does."""
        cancel = self._cancel
        if self.live_run != run_id or cancel is None:
            return conflict(f"{run_id} is not running live, so there is nothing to cancel")
        cancel.set()
        return response(202, {"run_id": run_id, "status": "cancelling"})

    def wait_for_live_run(self, timeout: float | None = None) -> bool:
        """For tests and shutdown: block until the live run, if any, has ended."""
        thread = self._live_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def static(self, path: str) -> site.File:
        if self.static_dir is None:
            return site.File(404, "application/json", json.dumps({"error": "no frontend is being served"}).encode())
        return site.serve(self.static_dir, path)


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
                served = app.static(url.path)
                payload = served.body
                self.send_response(served.status)
                self.send_header("content-type", served.content_type)
                if served.cache_control:
                    self.send_header("cache-control", served.cache_control)
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
