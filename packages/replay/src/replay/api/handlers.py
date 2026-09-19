"""The handlers. Each is an ordinary function: a parsed event and injected
dependencies in, an API Gateway response out.

    store    the log store - memory, SQLite or DynamoDB
    views    the view store the projector keeps current
    runner   how to build an agent and what to ask it, for the routes that run one

`list_runs` takes the view store and nothing else, so it cannot enumerate the log
even by accident.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from replay_events import BreakerTripped, Result, RunNotFound, canonical, dump_event

from ..agent import runs
from ..kernel import BreakerConfig, Breakers, diff_runs, flag_output, resolve, step_to_seq, trace_view
from ..kernel.breakers import CANCELLED
from ..projector import views as read_models
from .http import bad_request, conflict, created, not_found, ok, unavailable, body_of, int_param, path_param, query_of


@dataclass
class Runner:
    """What the run-driving routes need: an agent factory and the task.

    `live` says whether this deployment can run the agent at all. One with no
    model - replaying recordings only - sets it False, and the run-driving routes
    refuse with 503 rather than failing inside the agent. `model` names the model
    the factory uses, for GET /capabilities.

    `launch` decides how a live run is driven. None drives it to the end inside
    the request - one request, one run, which is what a Lambda does. A server
    that can run it in the background passes a function taking the new run's id,
    the job that drives it, and the fields to echo; it answers the request. The
    job takes a cancel flag - anything with is_set() - which the breakers check
    at every effect; `cancel` asks the server to set it for a live run.
    """

    factory: Callable[[], Any]
    prompt: str
    breakers: BreakerConfig = field(default_factory=BreakerConfig)
    live: bool = True
    model: str | None = None
    launch: Callable[[str, Callable[[Any], Any], dict], dict] | None = field(default=None, repr=False)
    cancel: Callable[[str], dict] | None = field(default=None, repr=False)


NOT_LIVE = "This deployment replays recordings and has no model to run the agent with."


def _not_live(runner: Runner | None):
    """A 503 if this deployment cannot run the agent, else None."""
    return unavailable(NOT_LIVE) if runner is None or not runner.live else None


def _launch(runner: Runner, run_id: str, job: Callable[[Any], Any], echo: dict[str, Any]):
    """Drive a live run: in the request by default, or however the server launches it."""
    if runner.launch is not None:
        return runner.launch(run_id, job, echo)
    outcome = job(None)  # driven inside the request: nothing can cancel it
    return created({**echo, "run_id": outcome.run_id, "created": True, **_outcome(outcome)})


def capabilities(event, *, runner: Runner | None = None, **_):
    """GET /capabilities - whether starting, forking and resuming can run live here."""
    live = runner is not None and runner.live
    return ok({"live": live, "model": runner.model if live else None, "reason": None if live else NOT_LIVE})


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:8]


def _exists(store, run_id: str) -> bool:
    try:
        store.get_metadata(run_id)
        return True
    except RunNotFound:
        return False


def _outcome(outcome) -> dict[str, Any]:
    return {"status": outcome.status.value, "answer": outcome.answer.strip() or None,
            "halted": None if outcome.halted is None else {"name": outcome.halted.name,
                                                           "detail": outcome.halted.detail}}


# ---------------------------------------------------------------- runs


def start_run(event, *, store, runner: Runner, **_):
    """POST /runs - record a run live. Idempotent by run_id."""
    body = body_of(event)
    run_id = body.get("run_id")
    if run_id is not None and _exists(store, run_id):
        return ok({"run_id": run_id, "created": False, **read_models.summary(resolve(store, run_id).events,
                                                                            store.get_metadata(run_id))})
    if (refused := _not_live(runner)) is not None:
        return refused
    run_id = run_id or runs.new_run_id()
    prompt = body.get("prompt", runner.prompt)
    return _launch(runner, run_id, lambda cancel: runs.record(store, runner.factory, prompt, run_id=run_id,
                                                              breakers=Breakers(runner.breakers, cancel=cancel)),
                   {"run_id": run_id})


def list_runs(event, *, views, **_):
    """GET /runs - from the projection. One Query; never a Scan of the log."""
    return ok({"runs": views.list_summaries()})


def get_run(event, *, store, **_):
    run_id = path_param(event, "id")
    return ok(read_models.run_view(resolve(store, run_id).events, store.get_metadata(run_id)))


def get_events(event, *, store, **_):
    """GET /runs/{id}/events?from=&to=&limit= - the resolved log, by eid."""
    run_id = path_param(event, "id")
    params = query_of(event)
    start = int_param(params, "from", 0)
    stop = int_param(params, "to")
    limit = int_param(params, "limit", 200)
    if limit is None or limit < 1:
        raise ValueError("limit must be at least 1")
    events = [e for e in resolve(store, run_id).events if e.eid >= start and (stop is None or e.eid < stop)]
    page = events[:limit]
    return ok({"run_id": run_id, "events": [dump_event(e) for e in page],
               "next": events[limit].eid if len(events) > limit else None})


# ---------------------------------------------------------------- fork and resume


def fork_run(event, *, store, runner: Runner, **_):
    """POST /runs/{id}/fork - {at_seq | at_step, mutation, run_id?}.

    Exactly one of at_seq and at_step. The UI speaks steps and the kernel speaks
    seqs; the mapping happens once, here, and both numbers go back in the
    response so the caller sees which step it actually forked.
    """
    parent = path_param(event, "id")
    body = body_of(event)
    has_seq, has_step = "at_seq" in body, "at_step" in body
    if has_seq == has_step:
        return bad_request("give exactly one of at_seq or at_step")
    if "mutation" not in body:
        return bad_request("mutation is required: it is the recorded result the fork replaces")

    log = resolve(store, parent)
    mapping = step_to_seq(log.events)
    if has_step:
        step = int(body["at_step"])
        if step not in mapping:
            return bad_request(f"{parent} has no step {step}", steps=sorted(mapping))
        seq = mapping[step]
    else:
        seq = int(body["at_seq"])
        step = next((s for s, q in mapping.items() if q == seq), None)
    if seq not in log.by_seq:
        return bad_request(f"{parent} has no effect at seq {seq}")

    # Derived from the fork point AND the mutation: the same request retried is
    # the same child, and a different value at the same step is a different one.
    child = body.get("run_id") or f"{parent}-fork-{seq}-{_digest(body['mutation'])}"
    echo = {"run_id": child, "parent_run_id": parent, "at_seq": seq, "at_step": step}
    if _exists(store, child):
        return ok({**echo, "created": False, "status": store.get_metadata(child).status.value})
    if (refused := _not_live(runner)) is not None:
        return refused
    mutation = Result(value=body["mutation"])
    return _launch(runner, child, lambda cancel: runs.fork(store, parent, seq, mutation, runner.factory,
                                                           runner.prompt, run_id=child,
                                                           breakers=Breakers(runner.breakers, cancel=cancel)), echo)


def resume_run(event, *, store, runner: Runner, **_):
    """POST /runs/{id}/resume - {breaker_overrides, run_id?}.

    breaker_overrides is required after a breaker halt. Without one, the resume
    replays to the halt and trips the same breaker at the same seq: a loop
    wearing a fix's clothes. A run the operator cancelled hit no ceiling, so it
    continues without one. Either way nothing runs before the request is checked.
    """
    run_id = path_param(event, "id")
    body = body_of(event)
    overrides = body.get("breaker_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return bad_request("breaker_overrides must be an object")
    unknown = sorted(set(overrides or {}) - set(BreakerConfig.model_fields))
    if unknown:
        return bad_request(f"unknown breaker settings: {unknown}", known=sorted(BreakerConfig.model_fields))

    log = resolve(store, run_id)
    trips = [e for e in log.events if isinstance(e, BreakerTripped)]
    if not trips:
        return conflict(f"{run_id} did not halt, so there is nothing to resume")
    cancelled = trips[-1].breaker == CANCELLED
    if not cancelled and not overrides:
        return bad_request("breaker_overrides is required: without one the resume re-trips the same breaker")
    overrides = overrides or {}
    child = body.get("run_id") or f"{run_id}-resume-{_digest(overrides)}"
    echo = {"run_id": child, "parent_run_id": run_id, "breaker_overrides": overrides}
    if _exists(store, child):
        return ok({**echo, "created": False, "status": store.get_metadata(child).status.value})
    if (refused := _not_live(runner)) is not None:
        return refused
    return _launch(runner, child, lambda cancel: runs.resume(store, run_id, runner.factory, runner.prompt,
                                                             breaker_overrides=overrides or None,
                                                             new_run_id_=child, cancel=cancel), echo)


def cancel_run(event, *, runner: Runner | None = None, **_):
    """POST /runs/{id}/cancel - stop a live run at its next effect.

    The run ends the way a breaker halt ends it: the refusal is appended, the run
    is marked tripped, and everything recorded before it is kept. An effect
    already in flight finishes and is recorded first - abandoning one mid-way
    would leave a request with no completion, which is what a crash looks like.
    """
    run_id = path_param(event, "id")
    if runner is None or runner.cancel is None:
        return conflict("this server drives each run inside its request, so there is no live run to cancel")
    return runner.cancel(run_id)


# ---------------------------------------------------------------- trace and diff


def trace_run(event, *, store, **_):
    """GET /runs/{id}/trace?event_id= - the whole chain, earliest first."""
    run_id = path_param(event, "id")
    event_id = int_param(query_of(event), "event_id")
    if event_id is None:
        return bad_request("event_id is required")
    events = resolve(store, run_id).events
    if not any(e.eid == event_id for e in events):
        return not_found(f"{run_id} has no event {event_id}")
    return ok({"run_id": run_id, **trace_view(events, event_id)})


def trace_output(event, *, store, **_):
    """GET /runs/{id}/trace/output - the same, from what the output was built from."""
    run_id = path_param(event, "id")
    events = resolve(store, run_id).events
    try:
        flagged = flag_output(events)
    except ValueError as exc:
        return not_found(str(exc))
    return ok({"run_id": run_id, **trace_view(events, flagged)})


def diff(event, *, store, **_):
    params = query_of(event)
    a, b = params.get("a"), params.get("b")
    if not a or not b:
        return bad_request("both a and b are required")
    if a == b:
        return bad_request("a and b must be different runs")
    for run_id in (a, b):
        if not _exists(store, run_id):
            return not_found(f"no such run: {run_id}")
    return ok(diff_runs(store, a, b))
