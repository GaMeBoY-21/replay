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


ANSWER = H.text_response("Reconciled at $492.00.")
# A response that asks for one more tool, so the run has a next effect.
ONE_MORE_TOOL = H.tool_response(H.tool_use("lookup_vendor", {"name": "Meridian Supplies"}, "call_one_more"))


class GatedModel(H.ScriptedModel):
    """Answers only once the gate opens: a model call held mid-flight.

    Its first response is `first`; every later one is the final answer."""

    def __init__(self, gate: threading.Event, entered: threading.Event, first=ANSWER) -> None:
        super().__init__([ANSWER])
        self.gate, self.entered, self.first, self.calls = gate, entered, first, 0

    async def stream(self, *args, **kwargs):
        self.entered.set()
        await asyncio.to_thread(self.gate.wait, 60)
        self.calls += 1
        for chunk in self.first if self.calls == 1 else ANSWER:
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
    first = {"response": ANSWER}  # a test sets ONE_MORE_TOOL here before it starts a run
    store = open_store(tmp_path / "local.db", CANONICAL)
    runner = Runner(factory=lambda: build_agent(model=GatedModel(gate, entered, first["response"])), prompt=TASK,
                    breakers=BreakerConfig(max_effects=80), model="gated test double")
    app = LocalApp(store, MemoryViewStore(), runner)
    httpd = serve(app, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        app.first = first
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


# ---------------------------------------------------------------- cancel


def own_events(app, run_id):
    return app.store.read(run_id)


def test_cancelling_a_live_run_ends_it_the_way_a_breaker_halt_does(live):
    base, app, gate, entered = live
    app.first["response"] = ONE_MORE_TOOL
    wrong = MANIFEST["wrong"]["run_id"]
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert status == 201 and entered.wait(20)
    child = forked["run_id"]

    status, body = call(base, "POST", f"/api/runs/{child}/cancel")
    assert (status, body["status"]) == (202, "cancelling")
    gate.set()  # the model call in flight finishes and is recorded; the cancel lands after it
    assert app.wait_for_live_run(30)

    assert app.store.get_metadata(child).status.value == "tripped"
    events = own_events(app, child)
    requested = {e.seq for e in events if e.type == "EffectRequested"}
    completed = {e.seq for e in events if e.type == "EffectCompleted"}
    assert requested == completed, "an effect was abandoned mid-way: the crash signature"
    (trip,) = [e for e in events if e.type == "BreakerTripped"]
    assert trip.breaker == "cancelled"
    assert not [e for e in events if e.eid > trip.eid and e.type == "EffectRequested"], "nothing ran after it"
    assert not any(ch.isdigit() for ch in trip.detail), "a breaker message names no step"

    run = call(base, "GET", f"/api/runs/{child}")[1]
    assert run["summary"]["halted"]["name"] == "cancelled"
    written = {w["key"]: w["value"] for s in run["steps"] for w in s["writes"]}
    assert written["invoice.currency"] == "INR", "everything recorded before the cancel is kept"


def test_only_the_live_run_can_be_cancelled(live):
    base, app, gate, entered = live
    status, body = call(base, "POST", f"/api/runs/{MANIFEST['wrong']['run_id']}/cancel")
    assert status == 409 and "not running live" in body["error"]


def test_a_cancelled_run_continues_without_raising_any_ceiling(live):
    base, app, gate, entered = live
    app.first["response"] = ONE_MORE_TOOL
    wrong, halted = MANIFEST["wrong"]["run_id"], MANIFEST["halted"]["run_id"]
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", fork_request())
    assert status == 201 and entered.wait(20)
    call(base, "POST", f"/api/runs/{forked['run_id']}/cancel")
    gate.set()
    assert app.wait_for_live_run(30)

    app.first["response"] = ANSWER
    status, resumed = call(base, "POST", f"/api/runs/{forked['run_id']}/resume", {})
    assert status == 201, resumed
    assert app.wait_for_live_run(30)
    assert app.store.get_metadata(resumed["run_id"]).status.value == "completed"

    # A real breaker halt still needs a raised ceiling.
    status, body = call(base, "POST", f"/api/runs/{halted}/resume", {})
    assert status == 400 and "breaker_overrides is required" in body["error"]


# ---------------------------------------------------------------- interrupted by a restart


def test_a_run_left_running_by_a_stopped_server_is_marked_interrupted_on_start(tmp_path):
    from replay.scenario.corpus import load_into
    from replay_events import RunStatus

    store = open_store(tmp_path / "local.db", CANONICAL)
    # What a server killed mid-run leaves behind: a log that stops, marked running.
    crashed = tmp_path / "crashed.json"
    body = json.loads((CANONICAL / f"{MANIFEST['wrong']['run_id']}.json").read_text())
    body["metadata"] = {**body["metadata"], "run_id": "crashed", "status": "running"}
    body["events"] = body["events"][:20]
    crashed.write_text(json.dumps(body))
    load_into(store, crashed)
    before = [e.eid for e in store.read("crashed")]

    app = LocalApp(store, MemoryViewStore(), None)
    assert app.interrupted == ["crashed"]
    assert store.get_metadata("crashed").status == RunStatus.INTERRUPTED
    after = store.read("crashed")
    assert [e.eid for e in after[:-1]] == before, "the recorded log is kept as it was"
    assert (after[-1].type, after[-1].status, after[-1].eid) == ("RunEnded", "interrupted", before[-1] + 1)

    # A second start finds nothing to close.
    assert LocalApp(store, MemoryViewStore(), None).interrupted == []


def test_a_run_that_finishes_before_the_cancel_lands_is_completed_not_relabelled(live):
    """A cancel stops the run at its next effect. If the call in flight was the
    last one, there is no next effect: the run finished, and says so."""
    base, app, gate, entered = live
    status, forked = call(base, "POST", f"/api/runs/{MANIFEST['wrong']['run_id']}/fork", fork_request())
    assert status == 201 and entered.wait(20)
    assert call(base, "POST", f"/api/runs/{forked['run_id']}/cancel")[0] == 202
    gate.set()
    assert app.wait_for_live_run(30)
    assert app.store.get_metadata(forked["run_id"]).status.value == "completed"
