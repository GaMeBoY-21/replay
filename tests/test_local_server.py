"""The local server, over real HTTP, serving the canonical runs end to end.

list -> open -> trace -> fork -> resume -> diff, against a SQLite log seeded from
the committed recordings. Anything that runs live after a fork point or a halt
runs against the scripted test double, so no model is called here.
"""

from __future__ import annotations

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
from replay.local.__main__ import main, make_runner, open_store
from replay.local.server import LocalApp, serve
from replay.scenario import TASK, load, substitution
from replay.scenario.live import build_agent
from replay.store.sqlite import SQLiteLogStore
from replay.store import MemoryLogStore
from replay.store.views import MemoryViewStore

pytestmark = pytest.mark.single_backend

CANONICAL = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "canonical"


@pytest.fixture
def server(tmp_path):
    store = SQLiteLogStore(tmp_path / "local.db")
    manifest = load(store, CANONICAL)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Replay</title>")
    runner = Runner(factory=lambda: build_agent(model=H.ScriptedModel([H.text_response("stopped here")])),
                    prompt=TASK, breakers=BreakerConfig(max_repeats=10**6, max_effects=80))
    app = LocalApp(store, MemoryViewStore(), runner, static_dir=static)
    httpd = serve(app, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", manifest
    httpd.shutdown()
    httpd.server_close()


def call(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            return error.code, json.loads(error.read())


def finished(base, run_id, timeout=30.0):
    """A live run answers at once; poll it, as the UI does, until it ends."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = call(base, "GET", f"/api/runs/{run_id}")[1]["summary"]["status"]
        if status != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"{run_id} was still running after {timeout}s")


def test_the_canonical_runs_end_to_end_over_http(server):
    base, manifest = server
    wrong = manifest["wrong"]["run_id"]

    status, listing = call(base, "GET", "/api/runs")
    assert status == 200
    listed = {row["run_id"]: row for row in listing["runs"]}
    assert {wrong, manifest["right"]["run_id"], manifest["fork"]["run_id"]} <= set(listed)

    status, opened = call(base, "GET", f"/api/runs/{wrong}")
    assert status == 200 and opened["summary"]["answer"] == listed[wrong]["answer"]

    status, traced = call(base, "GET", f"/api/runs/{wrong}/trace/output")
    assert status == 200
    assert (traced["head"]["key"], traced["head"]["value"]) == ("invoice.currency", "USD")

    # The same substitution the canonical fork used: the right run's own
    # recorded response at its decision.
    scratch = MemoryLogStore()
    load(scratch, CANONICAL)
    at, served = substitution(scratch, wrong, manifest["right"]["run_id"])
    mutation = served.value
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", {"at_seq": at, "mutation": mutation})
    assert status == 201 and forked["at_seq"] == at and forked["at_step"] is not None
    assert finished(base, forked["run_id"]) == "completed", "one live run at a time: let the fork end"

    status, listing = call(base, "GET", "/api/runs")
    assert forked["run_id"] in {row["run_id"] for row in listing["runs"]}, "the new fork was projected"

    halted = manifest["halted"]["run_id"]
    assert call(base, "POST", f"/api/runs/{halted}/resume", {})[0] == 400
    # Raise the ceiling of whichever breaker halted it, and only that one.
    raised = {"loop": "max_repeats", "depth": "max_effects"}[manifest["halt"]["breaker"]]
    status, resumed = call(base, "POST", f"/api/runs/{halted}/resume",
                           {"breaker_overrides": {raised: 10**6}})
    assert status == 201 and resumed["status"] in ("running", "completed")
    assert finished(base, resumed["run_id"]) == "completed"

    status, diffed = call(base, "GET", f"/api/diff?a={wrong}&b={forked['run_id']}")
    assert status == 200 and (diffed["shared_by"], diffed["shared_prefix"]) == ("storage", at)


def test_the_app_is_served_from_the_same_origin(server):
    base, _ = server
    with urllib.request.urlopen(base + "/", timeout=10) as response:
        assert b"<title>Replay</title>" in response.read()
    with urllib.request.urlopen(base + "/runs/some/client/route", timeout=10) as response:
        assert b"<title>Replay</title>" in response.read(), "the SPA handles its own routes"
    assert call(base, "GET", "/api/nowhere")[0] == 404


def test_seeding_loads_every_canonical_run_once(tmp_path):
    """`--seed` reads the manifest, loads parents before forks, and a second start
    against the same database adds nothing."""
    manifest = json.loads((CANONICAL / "manifest.json").read_text())
    expected = set(manifest["roots"]) | {manifest["fork"]["run_id"]}
    store = open_store(tmp_path / "local.db", CANONICAL)
    assert {m.run_id for m in store.list_runs()} == expected
    again = open_store(tmp_path / "local.db", CANONICAL)
    assert len(again.read(manifest["wrong"]["run_id"])) == len(store.read(manifest["wrong"]["run_id"]))


def test_replay_only_serves_the_recordings_and_runs_nothing_live(tmp_path):
    """--replay-only: /capabilities says not live, and fork and resume are refused
    with the reason - the state the UI shows as unavailable - while every read works."""
    store = open_store(tmp_path / "local.db", CANONICAL)
    app = LocalApp(store, MemoryViewStore(), make_runner(replay_only=True))
    httpd = serve(app, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        manifest = json.loads((CANONICAL / "manifest.json").read_text())
        status, caps = call(base, "GET", "/api/capabilities")
        assert (status, caps["live"], caps["model"]) == (200, False, None)
        halted = manifest["halted"]["run_id"]
        status, body = call(base, "POST", f"/api/runs/{halted}/resume", {"breaker_overrides": {"max_effects": 80}})
        assert status == 503 and "replays recordings" in body["error"]
        assert call(base, "GET", f"/api/runs/{manifest['wrong']['run_id']}/trace/output")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_flag_reaches_the_runner():
    assert make_runner(replay_only=True).live is False
    assert make_runner().live is True


def test_the_command_line_accepts_the_flag(monkeypatch, tmp_path):
    started = {}

    class Stop(Exception):
        pass

    def fake_serve(app, host, port):
        started["live"] = app.runner.live
        raise Stop

    monkeypatch.setattr("replay.local.__main__.serve", fake_serve)
    with pytest.raises(Stop):
        main(["--db", str(tmp_path / "x.db"), "--replay-only"])
    assert started == {"live": False}
