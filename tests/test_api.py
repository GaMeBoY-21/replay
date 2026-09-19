"""Every route, driven by synthetic API Gateway events, against every store.

The handlers are ordinary functions with the store injected, so nothing here
needs a deployed stack - and the local server calls exactly this `dispatch`.
Routes that run an agent run the scripted test double.
"""

from __future__ import annotations

import json

import pytest

import strands_harness as H
from backends import new_store, new_views
from replay.api import Runner, dispatch
from replay.api.app import ROUTES, http_event
from replay.kernel import BreakerConfig
from replay.projector.projector import project_run

MUTATION = {"toolUseId": "tooluse-2", "status": "success",
            "content": [{"text": json.dumps({"converted": 492.0, "rate": 0.012})}]}

LOOP = [H.tool_response(H.tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, f"loop-{n}"))
        for n in range(4)] + [H.text_response("done")]


class API:
    """Dispatch like the local server does: then project what was written."""

    def __init__(self, runner: Runner | None = None):
        self.store, self.views = new_store(), new_views()
        self.runner = runner or Runner(factory=H.build_agent, prompt=H.PROMPT,
                                       breakers=BreakerConfig(max_repeats=10**6))

    def __call__(self, method, path, *, query=None, body=None, **deps):
        result = dispatch(http_event(method, path, query=query, body=body),
                          store=self.store, views=self.views, runner=self.runner, **deps)
        payload = json.loads(result["body"])
        if method == "POST" and result["statusCode"] < 300:
            project_run(self.store, self.views, payload["run_id"])
        return result["statusCode"], payload


@pytest.fixture
def api():
    client = API()
    status, _ = client("POST", "/runs", body={"run_id": "run-1"})
    assert status == 201
    return client


@pytest.fixture
def halted():
    looping = Runner(factory=lambda: H.build_agent(model=H.ScriptedModel(LOOP)), prompt=H.PROMPT,
                     breakers=BreakerConfig(max_repeats=3))
    client = API(looping)
    status, body = client("POST", "/runs", body={"run_id": "halted"})
    assert status == 201 and body["status"] == "tripped"
    return client


# ---------------------------------------------------------------- runs


def test_starting_a_run_twice_is_idempotent(api):
    status, body = api("POST", "/runs", body={"run_id": "run-1"})
    assert (status, body["created"], body["run_id"]) == (200, False, "run-1")
    assert [m.run_id for m in api.store.list_runs()] == ["run-1"]


def test_the_list_comes_from_the_projection(api):
    status, body = api("GET", "/runs")
    assert status == 200
    (row,) = body["runs"]
    assert (row["run_id"], row["status"], row["answer"]) == ("run-1", "completed", "Total: $41,000.00")


def test_the_list_never_touches_the_log(api):
    """Structural: the handler gets a log store that fails on any use, and still
    answers. A Scan cannot hide in a handler that has no log to scan."""

    class NoLog:
        def __getattr__(self, name):
            raise AssertionError(f"GET /runs touched the log store: {name}")

    result = dispatch(http_event("GET", "/runs"), store=NoLog(), views=api.views, runner=api.runner)
    assert result["statusCode"] == 200
    assert [r["run_id"] for r in json.loads(result["body"])["runs"]] == ["run-1"]


def test_a_run_opens_with_its_steps_and_step_to_seq(api):
    status, body = api("GET", "/runs/run-1")
    assert status == 200
    assert body["summary"]["answer"] == "Total: $41,000.00"
    assert body["step_to_seq"]["1"] == 0
    assert [s["name"] for s in body["steps"]][:3] == ["model", "read_invoice_header", "convert_currency"]


def test_an_unknown_run_is_404(api):
    assert api("GET", "/runs/nope")[0] == 404
    assert api("GET", "/runs/nope/trace/output")[0] == 404


def test_events_page_by_eid(api):
    status, first = api("GET", "/runs/run-1/events", query={"from": 0, "limit": 5})
    assert status == 200 and [e["eid"] for e in first["events"]] == [0, 1, 2, 3, 4]
    _, second = api("GET", "/runs/run-1/events", query={"from": first["next"], "limit": 5})
    assert second["events"][0]["eid"] == first["next"] == 5
    _, bounded = api("GET", "/runs/run-1/events", query={"from": 2, "to": 4})
    assert [e["eid"] for e in bounded["events"]] == [2, 3]


def test_a_non_integer_page_bound_is_a_client_error(api):
    assert api("GET", "/runs/run-1/events", query={"from": "x"})[0] == 400


def test_a_malformed_body_is_a_client_error(api):
    assert api("POST", "/runs", body="{not json")[0] == 400


# ---------------------------------------------------------------- fork


def test_fork_by_step_and_by_seq_is_the_same_child_and_echoes_both(api):
    by_step = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": MUTATION})
    by_seq = api("POST", "/runs/run-1/fork", body={"at_seq": 2, "mutation": MUTATION})

    assert by_step[0] == 201 and by_seq[0] == 200, "the second request finds the first's child"
    assert by_step[1]["run_id"] == by_seq[1]["run_id"]
    assert (by_step[1]["at_step"], by_step[1]["at_seq"]) == (3, 2)
    assert (by_seq[1]["at_step"], by_seq[1]["at_seq"]) == (3, 2)


def test_a_retried_fork_returns_the_same_run(api):
    first = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": MUTATION})
    again = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": MUTATION})
    assert (first[0], again[0]) == (201, 200)
    assert again[1]["run_id"] == first[1]["run_id"] and again[1]["created"] is False
    assert len(api.store.list_runs()) == 2


def test_a_different_mutation_at_the_same_step_is_a_different_fork(api):
    first = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": MUTATION})
    other = dict(MUTATION, content=[{"text": "something else"}])
    second = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": other})
    assert second[0] == 201 and second[1]["run_id"] != first[1]["run_id"]


def test_fork_takes_exactly_one_fork_point(api):
    assert api("POST", "/runs/run-1/fork", body={"at_step": 3, "at_seq": 2, "mutation": MUTATION})[0] == 400
    assert api("POST", "/runs/run-1/fork", body={"mutation": MUTATION})[0] == 400
    assert api("POST", "/runs/run-1/fork", body={"at_step": 99, "mutation": MUTATION})[0] == 400
    assert api("POST", "/runs/run-1/fork", body={"at_step": 3})[0] == 400
    assert len(api.store.list_runs()) == 1, "a rejected fork records nothing"


# ---------------------------------------------------------------- resume


def test_resume_without_overrides_is_refused_before_anything_runs(halted):
    for body in ({}, {"breaker_overrides": {}}, {"breaker_overrides": "high"}):
        assert halted("POST", "/runs/halted/resume", body=body)[0] == 400
    assert halted("POST", "/runs/halted/resume", body={"breaker_overrides": {"loops": 9}})[0] == 400
    assert [m.run_id for m in halted.store.list_runs()] == ["halted"]


def test_resume_with_an_override_completes_and_a_retry_returns_it(halted):
    status, body = halted("POST", "/runs/halted/resume", body={"breaker_overrides": {"max_repeats": 10}})
    assert (status, body["status"], body["answer"]) == (201, "completed", "done")
    again = halted("POST", "/runs/halted/resume", body={"breaker_overrides": {"max_repeats": 10}})
    assert again[0] == 200 and again[1]["run_id"] == body["run_id"]


def test_a_run_that_never_halted_cannot_be_resumed(api):
    assert api("POST", "/runs/run-1/resume", body={"breaker_overrides": {"max_repeats": 10}})[0] == 409


def test_the_halted_run_projects_its_halted_step(halted):
    _, body = halted("GET", "/runs")
    (row,) = body["runs"]
    assert row["status"] == "tripped"
    assert row["halted"]["name"] == "loop" and row["halted"]["step"] is not None


# ---------------------------------------------------------------- trace and diff


def test_trace_from_the_output_and_from_an_event(api):
    status, output = api("GET", "/runs/run-1/trace/output")
    assert status == 200
    # The double's last state read is record_total reading fx.rate, so that is
    # what the output is traced from.
    assert output["flagged"]["key"] == "fx.rate"
    assert [link["key"] for link in output["chain"]] == ["invoice.currency", "fx.rate"]

    status, by_event = api("GET", "/runs/run-1/trace", query={"event_id": output["flagged"]["eid"]})
    assert status == 200 and by_event["chain"] == output["chain"]


def test_trace_requires_an_event_in_the_run(api):
    assert api("GET", "/runs/run-1/trace")[0] == 400
    assert api("GET", "/runs/run-1/trace", query={"event_id": 99999})[0] == 404


def test_diff_aligns_a_fork_with_its_parent(api):
    _, fork = api("POST", "/runs/run-1/fork", body={"at_step": 3, "mutation": MUTATION})
    status, body = api("GET", "/diff", query={"a": "run-1", "b": fork["run_id"]})
    assert status == 200
    assert (body["shared_prefix"], body["shared_by"], body["divergence_seq"]) == (2, "storage", 2)


def test_diff_needs_two_different_known_runs(api):
    assert api("GET", "/diff", query={"a": "run-1"})[0] == 400
    assert api("GET", "/diff", query={"a": "run-1", "b": "run-1"})[0] == 400
    assert api("GET", "/diff", query={"a": "run-1", "b": "nope"})[0] == 404


# ---------------------------------------------------------------- the router


def test_the_api_prefix_is_stripped_once_at_the_router(api):
    assert api("GET", "/api/runs")[1] == api("GET", "/runs")[1]
    assert api("GET", "/api/runs/run-1")[0] == 200
    assert api("GET", "/apiary/runs")[0] == 404, "only a whole /api segment is a prefix"


def test_no_route_handles_the_prefix_itself():
    """If a route matched /api itself, a handler could depend on it and the
    router's stripping would be one place among several."""
    assert all("api" not in pattern.pattern for _, pattern, _ in ROUTES)


def test_unknown_routes_and_methods_are_404(api):
    assert api("GET", "/nowhere")[0] == 404
    assert api("DELETE", "/runs/run-1")[0] == 404


# ---------------------------------------------------------------- a deployment with no model


def test_capabilities_say_whether_the_agent_can_run_live(api):
    status, body = api("GET", "/capabilities")
    assert (status, body["live"], body["reason"]) == (200, True, None)
    named = API(Runner(factory=H.build_agent, prompt=H.PROMPT, model="qwen2.5:14b"))
    assert named("GET", "/capabilities")[1]["model"] == "qwen2.5:14b"


def test_a_deployment_with_no_model_says_so_and_refuses_to_run_anything(halted):
    """Replaying recordings needs no model; running the agent does. Without one,
    start, fork and resume are refused with the reason - before anything is
    written - instead of failing inside the agent."""
    recordings_only = API(Runner(factory=H.build_agent, prompt=H.PROMPT, live=False))
    recordings_only.store, recordings_only.views = halted.store, halted.views
    before = {m.run_id for m in halted.store.list_runs()}

    status, body = recordings_only("GET", "/capabilities")
    assert (status, body["live"], body["model"]) == (200, False, None)
    assert "replays recordings" in body["reason"]

    mutation = {"toolUseId": "t", "status": "success", "content": [{"text": "{}"}]}
    for method, path, payload in [
        ("POST", "/runs", {"run_id": "new"}),
        ("POST", "/runs/halted/fork", {"at_step": 1, "mutation": mutation}),
        ("POST", "/runs/halted/resume", {"breaker_overrides": {"max_repeats": 10**6}}),
    ]:
        status, body = recordings_only(method, path, body=payload)
        assert status == 503 and "replays recordings" in body["error"], path

    assert {m.run_id for m in halted.store.list_runs()} == before
    assert recordings_only("GET", "/runs/halted")[0] == 200, "reading still works"


def test_with_no_runner_at_all_nothing_claims_to_be_live():
    client = API()
    client.runner = None
    assert client("GET", "/capabilities")[1]["live"] is False
