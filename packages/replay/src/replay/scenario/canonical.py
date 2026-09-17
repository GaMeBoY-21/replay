"""The three canonical runs, and the fixtures built from them.

    Run B   the completed, wrong run: forty steps, "Total: $41,000.00"
    Run A   the same run with the loop breaker armed at five: halts at step 24
    Fork A  Run B forked at step 12 with the corrected exchange rate: "$492.00"

They are recorded against the scripted provider with a counting clock, so the
fixtures regenerate byte for byte and a test can require that the committed
ones are current. Recording against Bedrock later swaps the provider and nothing
else.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from strands import Agent

from replay_events import Result, dump_event

from ..agent import runs
from ..kernel import BreakerConfig, Breakers, diff_runs, flag_output, resolve, step_to_seq, trace_view
from .data import SYSTEM_PROMPT, TASK
from .model import ScenarioModel
from .tools import TOOLS

RUN_B = "run-b"
RUN_A = "run-a"
FORK_A = "fork-a"

FORK_STEP = 12
SUBSTITUTED = {"rate": 0.012, "converted": 492, "warning": "source currency INR, not USD"}
LOOP_CEILING = 5


def build_agent(model=None) -> Agent:
    return Agent(
        model=model if model is not None else ScenarioModel(),
        tools=list(TOOLS),
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


def counting_clock(start: datetime = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)):
    """One second per event, from a fixed instant."""
    ticks = iter(range(10**9))
    return lambda: start + timedelta(seconds=next(ticks))


def record_run_b(store, *, clock=None):
    # Not armed against loops: Run B is the run that completes, wrongly.
    return runs.record(store, build_agent, TASK, run_id=RUN_B,
                       breakers=Breakers(BreakerConfig(max_repeats=10**6)), clock=clock)


def record_run_a(store, *, clock=None):
    return runs.record(store, build_agent, TASK, run_id=RUN_A,
                       breakers=Breakers(BreakerConfig(max_repeats=LOOP_CEILING)), clock=clock)


def substitution(store, run_id: str, step: int, payload: dict[str, Any]) -> tuple[int, Result]:
    """The seq behind a step, and a tool result replacing the recorded one there.

    The step is translated with step_to_seq - never passed through as a seq.
    """
    log = resolve(store, run_id)
    seq = step_to_seq(log.events)[step]
    recorded = log.at(seq).result.value
    replaced = dict(recorded, content=[{"text": json.dumps(payload)}])
    return seq, Result(value=replaced)


def fork_run_b(store, *, run_id: str = FORK_A, clock=None):
    seq, mutation = substitution(store, RUN_B, FORK_STEP, SUBSTITUTED)
    return runs.fork(store, RUN_B, seq, mutation, build_agent, TASK, run_id=run_id,
                     breakers=Breakers(BreakerConfig(max_repeats=10**6)), clock=clock)


def resume_run_a(store, *, run_id: str, breaker_overrides: dict | None = None, clock=None):
    return runs.resume(store, RUN_A, build_agent, TASK, breaker_overrides=breaker_overrides,
                       new_run_id_=run_id, clock=clock)


def record_canonical(store) -> dict[str, Any]:
    """Record all three runs into a store. Returns their outcomes by run id."""
    return {
        RUN_B: record_run_b(store, clock=counting_clock()),
        RUN_A: record_run_a(store, clock=counting_clock()),
        FORK_A: fork_run_b(store, clock=counting_clock()),
    }


def fixtures(store) -> dict[str, Any]:
    """Everything the frontend needs, as JSON-ready data, from recorded runs."""
    out: dict[str, Any] = {}
    for run_id in (RUN_B, RUN_A, FORK_A):
        log = resolve(store, run_id)
        out[run_id] = {
            "metadata": store.get_metadata(run_id).model_dump(mode="json"),
            "stored_event_count": len(store.read(run_id)),
            "step_to_seq": {str(step): seq for step, seq in step_to_seq(log.events).items()},
            "events": [dump_event(event) for event in log.events],
        }
    run_b = resolve(store, RUN_B).events
    out[f"trace-{RUN_B}"] = trace_view(run_b, flag_output(run_b))
    out[f"diff-{RUN_B}-{FORK_A}"] = diff_runs(store, RUN_B, FORK_A)
    return out
