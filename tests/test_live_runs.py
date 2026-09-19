"""Live runs on the local server: reads never wait behind one.

A live fork or resume against a local model takes one to two minutes. The model
here blocks mid-call until the test releases it, so a live run is genuinely in
progress - holding the one live-run lock - while the test makes its reads.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import threading
import time
import urllib.request
from urllib.error import HTTPError

import pytest

import strands_harness as H
from replay.api import Runner
from replay.kernel import BreakerConfig
from replay.local.__main__ import open_store
from replay.local.server import LocalApp, serve
from replay.scenario import TASK, substitution
from replay.scenario.live import build_agent
from replay.store import MemoryLogStore
from replay.store.views import MemoryViewStore

pytestmark = pytest.mark.single_backend

CANONICAL = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "canonical"
MANIFEST = json.loads((CANONICAL / "manifest.json").read_text())
READ_BUDGET_S = 2.0


class GatedModel(H.ScriptedModel):
    """Answers only once the gate opens: a model call held mid-flight."""

    def __init__(self, gate: threading.Event, entered: threading.Event) -> None:
        super().__init__([H.text_response("Reconciled at $492.00.") for _ in range(4)])
        self.gate, self.entered = gate, entered

    async def stream(self, *args, **kwargs):
        self.entered.set()
        await asyncio.to_thread(self.gate.wait, 60)
        async for chunk in super().stream(*args, **kwargs):
            yield chunk


def call(base, method, path, body=None, timeout=10.0):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


@pytest.fixture
def live(tmp_path):
    gate, entered = threading.Event(), threading.Event()
    store = open_store(tmp_path / "local.db", CANONICAL)
    runner = Runner(factory=lambda: build_agent(model=GatedModel(gate, entered)), prompt=TASK,
                    breakers=BreakerConfig(max_effects=80), model="gated test double")
    app = LocalApp(store, MemoryViewStore(), runner)
    httpd = serve(app, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", app, gate, entered
    finally:
        gate.set()
        app.wait_for_live_run(30)
        httpd.shutdown()
        httpd.server_close()


def fork_request():
    scratch = MemoryLogStore()
    from replay.scenario import load

    load(scratch, CANONICAL)
    at, served = substitution(scratch, MANIFEST["wrong"]["run_id"], MANIFEST["right"]["run_id"])
    return {"at_seq": at, "mutation": served.value}


def timed(base, path):
    started = time.monotonic()
    status, body = call(base, "GET", path, timeout=READ_BUDGET_S * 3)
    return status, body, time.monotonic() - started


def test_a_read_returns_while_a_live_run_is_in_progress(live):
    base, app, gate, entered = live
    wrong = MANIFEST["wrong"]["run_id"]

    started = time.monotonic()
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert status == 201 and forked["status"] == "running"
    assert time.monotonic() - started < READ_BUDGET_S, "the request waited for the run instead of starting it"

    assert entered.wait(20), "the live run never reached the model"
    assert app.live_run == forked["run_id"] and not gate.is_set()

    for path in ["/api/runs", f"/api/runs/{wrong}/trace/output", f"/api/diff?a={wrong}&b={MANIFEST['right']['run_id']}"]:
        status, _, took = timed(base, path)
        assert status == 200 and took < READ_BUDGET_S, f"{path} waited {took:.1f}s behind the live run"

    # The live run itself can be read as it goes: its steps so far, marked running.
    status, run, took = timed(base, f"/api/runs/{forked['run_id']}")
    assert status == 200 and took < READ_BUDGET_S
    assert run["summary"]["status"] == "running"
    written = {w["key"]: w["value"] for s in run["steps"] for w in s["writes"]}
    assert written["invoice.currency"] == "INR", "the tools after the fork point already ran live"
    listed = {r["run_id"]: r["status"] for r in call(base, "GET", "/api/runs")[1]["runs"]}
    assert listed[forked["run_id"]] == "running"

    gate.set()
    assert app.wait_for_live_run(30)
    assert call(base, "GET", f"/api/runs/{forked['run_id']}")[1]["summary"]["status"] == "completed"
    listed = {r["run_id"]: r["status"] for r in call(base, "GET", "/api/runs")[1]["runs"]}
    assert listed[forked["run_id"]] == "completed", "the run was re-projected when it ended"


def test_one_live_run_at_a_time_and_the_second_is_told_which(live):
    base, app, gate, entered = live
    wrong, halted = MANIFEST["wrong"]["run_id"], MANIFEST["halted"]["run_id"]
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert status == 201 and entered.wait(20)

    started = time.monotonic()
    status, refused = call(base, "POST", f"/api/runs/{halted}/resume", {"breaker_overrides": {"max_effects": 80}})
    assert status == 409 and forked["run_id"] in refused["error"]
    assert time.monotonic() - started < READ_BUDGET_S, "the second request queued behind the first"

    gate.set()
    assert app.wait_for_live_run(30)
    status, resumed = call(base, "POST", f"/api/runs/{halted}/resume", {"breaker_overrides": {"max_effects": 80}})
    assert status == 201, "the lock is released when the live run ends"
    assert app.wait_for_live_run(30)


def test_a_retried_request_for_a_running_run_returns_it_without_starting_another(live):
    base, app, gate, entered = live
    wrong = MANIFEST["wrong"]["run_id"]
    status, first = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert status == 201 and entered.wait(20)
    status, again = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert (status, again["created"], again["run_id"], again["status"]) == (200, False, first["run_id"], "running")
