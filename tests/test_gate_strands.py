"""The gate: a real Strands agent records and replays byte-identically, and the
real tool does not execute during replay.

The proof that no tool ran is a module-level counter the tool functions
increment, not a return value. A return value can match a recording by
coincidence; the counter cannot.
"""

from __future__ import annotations

import pytest

import strands_harness as H
from replay_events import EFFECT_EVENT_TYPES, canonical, normalise_messages


@pytest.fixture
def recorded():
    H.reset_witnesses()
    store, agent, answer = H.record()
    return store, agent, answer


def test_the_recording_really_executed_the_tools(recorded):
    """The witness is live ammunition: recording moves it. Without this, an
    unmoved witness on replay would prove nothing."""
    _, _, answer = recorded
    assert H.TOOL_EXECUTIONS == 3
    assert H.MODEL_CALLS == 3
    assert answer.strip() == "Total: $41,000.00"


def test_replay_executes_no_real_tool(recorded):
    store, _, _ = recorded
    H.reset_witnesses()

    H.replay(store)

    assert H.TOOL_EXECUTIONS == 0, "a real tool ran during replay"
    assert H.MODEL_CALLS == 0, "the provider was called during replay"


def test_replay_is_byte_identical(recorded):
    store, live_agent, live_answer = recorded

    _, replayed_agent, replayed_answer = H.replay(store)

    assert replayed_answer == live_answer
    assert canonical(normalise_messages(replayed_agent.messages)) == canonical(
        normalise_messages(live_agent.messages)
    )
    live_state = H.raw_state(live_agent)
    assert live_state == {"invoice.currency": "USD", "fx.rate": 1.0, "report.total": 41000}
    assert canonical(H.raw_state(replayed_agent)) == canonical(live_state)


def test_replay_records_no_effects_of_its_own(recorded):
    store, _, _ = recorded

    replay_store, _, _ = H.replay(store)

    own = [e for e in replay_store.read("strands-run-replay") if e.type in EFFECT_EVENT_TYPES]
    assert own == []


def test_the_recording_holds_every_effect_the_run_performed(recorded):
    store, _, _ = recorded
    events = store.read("strands-run")
    requested = [e for e in events if e.type == "EffectRequested"]
    completed = [e for e in events if e.type == "EffectCompleted"]

    assert [e.effect.describe() for e in requested] == [
        "model(1 messages)",
        "tool(read_invoice_header)",
        "tool(convert_currency)",
        "model(3 messages)",
        "tool(record_total)",
        "model(5 messages)",
    ]
    assert [e.seq for e in requested] == [e.seq for e in completed] == list(range(6))


def test_a_replay_that_asks_for_something_else_diverges(recorded):
    """Replay is compared, not merely served: a different prompt is a different
    first model call, and it must fail rather than serve the recorded answer."""
    from replay.kernel import DivergenceError

    store, _, _ = recorded
    with pytest.raises(Exception) as caught:
        H.replay(store, prompt="Reconcile a different invoice.")
    assert isinstance(H.root_cause(caught.value), DivergenceError)
