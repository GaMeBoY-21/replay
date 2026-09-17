"""Breakers on real runs, and the two unrecorded model inputs.

The first of these is here because a hand-built dict hid it: the budget breaker
counted zero tokens on every real Strands run, because a recorded model result
is a chunk list and only the dict path was ever tested.
"""

from __future__ import annotations

from backends import new_store
import asyncio

import pytest
from strands.agent.agent_metadata import AgentMetadata

import strands_harness as H
from replay.agent import ReplayModel, UnrecordedModelInput, attach
from replay.kernel import BreakerConfig, Breakers, LiveMode, RunContext, load_log
from replay.kernel.breakers import extract_tokens
from replay.store import MemoryLogStore


def run(model_script, config, run_id):
    store = new_store()
    ctx = RunContext(run_id, store, LiveMode(), breakers=Breakers(config))
    agent = H.build_agent(model=H.ScriptedModel(model_script))
    attach(agent, ctx)
    H.reset_witnesses()
    answer = str(agent(H.PROMPT))  # a halt must not raise
    return store, answer


def test_tokens_are_counted_from_a_real_recorded_run():
    store, _, _ = H.record()
    completed = [e for e in store.read("strands-run") if e.type == "EffectCompleted"
                 and isinstance(e.result.value, list)]
    counted = [extract_tokens(e.result) for e in completed]
    assert len(counted) == 3
    assert counted == [49, 49, 49]


def test_the_budget_breaker_trips_on_a_real_run():
    """Tokens are accounted when a model call completes, and the next effect after
    a model call is a tool call, so the budget trips on the tool side here."""
    store, answer = run(H.SCRIPT, BreakerConfig(max_tokens=60, max_repeats=10**9), "budget")

    assert "halted by the budget breaker" in answer
    assert H.MODEL_CALLS == 2, "nothing past the budget may run"
    trips = [e for e in store.read("budget") if e.type == "BreakerTripped"]
    assert [t.breaker for t in trips] == ["budget"]


LOOP = [
    H.tool_response(H.tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, f"loop-{n}"))
    for n in range(10)
]


def halted_state(store, run_id):
    events = store.read(run_id)
    trip = next(e for e in events if e.type == "BreakerTripped")
    requested = [e.seq for e in events if e.type == "EffectRequested"]
    return {
        "tail": [e.type for e in events][-2:],
        "trip_seq_is_next": trip.seq == max(requested) + 1,
        "incomplete": load_log(store, run_id).incomplete,
    }


def test_a_model_trip_and_a_tool_trip_end_in_the_same_state():
    """The script's effects are model 0, tools 1 and 2, model 3. A depth ceiling
    of 3 therefore trips on a MODEL call - the only effect-shape-independent
    ceiling that can land there, since a tool call always checks first after a
    model call has added tokens."""
    tool_store, tool_answer = run(LOOP, BreakerConfig(max_repeats=3), "tool-trip")
    model_store, model_answer = run(H.SCRIPT, BreakerConfig(max_effects=3, max_repeats=10**9), "model-trip")

    assert tool_answer.startswith("halted by the loop breaker")
    assert model_answer.startswith("halted by the depth breaker")
    assert H.MODEL_CALLS == 1, "the model call at the ceiling must not be made"
    (trip,) = [e for e in model_store.read("model-trip") if e.type == "BreakerTripped"]
    assert trip.seq == 3
    expected = {"tail": ["BreakerTripped", "StepBoundary"], "trip_seq_is_next": True, "incomplete": []}
    assert halted_state(tool_store, "tool-trip") == expected
    assert halted_state(model_store, "model-trip") == expected


def drain(model, **kwargs):
    async def collect():
        return [c async for c in model.stream([{"role": "user", "content": [{"text": "hi"}]}], **kwargs)]
    return asyncio.run(collect())


def fresh_model():
    ctx = RunContext("inputs", new_store(), LiveMode(), breakers=H.unbounded())
    return ReplayModel(ctx, H.ScriptedModel([H.text_response("ok")]))


def test_empty_unrecorded_inputs_are_accepted():
    assert drain(fresh_model(), model_state={}, agent_metadata=AgentMetadata())


def test_a_non_empty_model_state_is_refused():
    with pytest.raises(UnrecordedModelInput, match="model_state.*unrecorded model input"):
        drain(fresh_model(), model_state={"conversation": "abc"})


def test_a_non_empty_agent_metadata_is_refused():
    with pytest.raises(UnrecordedModelInput, match="agent_metadata.*unrecorded model input"):
        drain(fresh_model(), agent_metadata=AgentMetadata(session_id="session-1"))
