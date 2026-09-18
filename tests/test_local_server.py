"""The local server, over real HTTP, serving the canonical runs end to end.

list -> open -> trace -> fork -> resume -> diff, against a SQLite log seeded from
the committed recordings. Anything that runs live after a fork point or a halt
runs against the scripted test double, so no model is called here.
"""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

pytest.skip(
    "pending: awaits the canonical runs from stage 5 (a real-model wrong run, its fork "
    "and a halted run in fixtures/canonical), which are not recorded yet",
    allow_module_level=True,
)

import strands_harness as H  # noqa: E402
from replay.api import Runner  # noqa: E402
from replay.kernel import BreakerConfig  # noqa: E402
from replay.local.server import LocalApp, serve  # noqa: E402
from replay.scenario import SUBSTITUTED, TASK, load  # noqa: E402
from replay.scenario.live import build_agent  # noqa: E402
from replay.store.sqlite import SQLiteLogStore  # noqa: E402
from replay.store.views import MemoryViewStore  # noqa: E402

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


def call(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


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

    at = manifest["fork"]["at_seq"]
    mutation = {"toolUseId": "replaced", "status": "success", "content": [{"text": json.dumps(SUBSTITUTED)}]}
    status, forked = call(base, "POST", f"/api/runs/{wrong}/fork", {"at_seq": at, "mutation": mutation})
    assert status == 201 and forked["at_seq"] == at and forked["at_step"] is not None

    status, listing = call(base, "GET", "/api/runs")
    assert forked["run_id"] in {row["run_id"] for row in listing["runs"]}, "the new fork was projected"

    halted = manifest["halted"]["run_id"]
    assert call(base, "POST", f"/api/runs/{halted}/resume", {})[0] == 400
    status, resumed = call(base, "POST", f"/api/runs/{halted}/resume",
                           {"breaker_overrides": {"max_repeats": 10**6}})
    assert status == 201 and resumed["status"] == "completed"

    status, diffed = call(base, "GET", f"/api/diff?a={wrong}&b={forked['run_id']}")
    assert status == 200 and (diffed["shared_by"], diffed["shared_prefix"]) == ("storage", at)


def test_the_app_is_served_from_the_same_origin(server):
    base, _ = server
    with urllib.request.urlopen(base + "/", timeout=10) as response:
        assert b"<title>Replay</title>" in response.read()
    with urllib.request.urlopen(base + "/runs/some/client/route", timeout=10) as response:
        assert b"<title>Replay</title>" in response.read(), "the SPA handles its own routes"
    assert call(base, "GET", "/api/nowhere")[0] == 404
