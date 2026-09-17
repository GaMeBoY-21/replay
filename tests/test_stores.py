"""The store protocol, asserted identically against every backend.

These run once per backend like the rest of the suite. Nothing here names a
store: if a test could tell which one it was on, the seam would be leaking.
"""

from __future__ import annotations

import pytest
from strands import tool

import strands_harness as H
from backends import new_store
from replay.agent import runs
from replay.store import EventIdConflict
from replay_events import EffectCompleted, Result, RunMetadata, RunNotFound, RunStatus, StepBoundary

BIG = "x" * 150_000  # past the 100KB inline limit


def test_append_is_conditional_on_the_eid_being_free():
    store = new_store()
    store.append("run", StepBoundary(eid=0))
    with pytest.raises(EventIdConflict):
        store.append("run", StepBoundary(eid=0, label="second writer"))
    assert [e.label for e in store.read("run")] == [None], "the duplicate replaced the original"


def test_a_run_reads_back_in_eid_order_whatever_the_append_order():
    """Lexicographic order on an unpadded key puts 10 before 2 and 100 before 11."""
    store = new_store()
    for eid in (100, 2, 11, 10, 0, 1):
        store.append("run", StepBoundary(eid=eid))
    assert [e.eid for e in store.read("run")] == [0, 1, 2, 10, 11, 100]


def test_runs_are_isolated_from_each_other():
    store = new_store()
    store.append("a", StepBoundary(eid=0))
    store.append("b", StepBoundary(eid=0))
    assert len(store.read("a")) == len(store.read("b")) == 1
    assert store.read("never") == []


def test_a_value_comes_back_in_its_recorded_form():
    """Keys sorted, a tuple as a list, an int still an int and a float still a
    float. The live object would satisfy the memory store and nothing else."""
    store = new_store()
    value = {"zeta": (1, 2), "alpha": 1, "ratio": 0.5, "flag": True, "nested": {"b": 1, "a": None}}
    store.append("run", EffectCompleted(eid=0, seq=0, result=Result(value=value)))

    (read,) = store.read("run")
    served = read.result.value
    assert list(served) == ["alpha", "flag", "nested", "ratio", "zeta"]
    assert list(served["nested"]) == ["a", "b"]
    assert served["zeta"] == [1, 2] and type(served["zeta"]) is list
    assert type(served["alpha"]) is int and type(served["ratio"]) is float and served["flag"] is True


def test_metadata_is_written_replaced_and_listed():
    store = new_store()
    with pytest.raises(RunNotFound):
        store.get_metadata("run-b")

    store.put_metadata(RunMetadata(run_id="run-b", breaker_config={"max_repeats": 3}))
    store.put_metadata(RunMetadata(run_id="run-a"))
    store.put_metadata(store.get_metadata("run-b").model_copy(update={"status": RunStatus.TRIPPED}))

    assert store.get_metadata("run-b").status == RunStatus.TRIPPED
    assert store.get_metadata("run-b").breaker_config == {"max_repeats": 3}
    assert [m.run_id for m in store.list_runs()] == ["run-a", "run-b"]


def test_a_payload_over_100kb_round_trips():
    store = new_store()
    store.append("run", EffectCompleted(eid=0, seq=0, result=Result(value={"blob": BIG})))
    store.append("run", StepBoundary(eid=1))
    events = store.read("run")
    assert events[0].result.value == {"blob": BIG}
    assert [e.eid for e in events] == [0, 1]


LARGE_CALLS = 0


@tool
def fetch_attachment(invoice_id: str) -> str:
    """Fetch an invoice's attached PDF, as text."""
    global LARGE_CALLS
    LARGE_CALLS += 1
    return BIG


def large():
    script = [
        H.tool_response(H.tool_use("fetch_attachment", {"invoice_id": "INV-2291"}, "attach-1")),
        H.text_response("attachment read"),
    ]
    return H.build_agent(model=H.ScriptedModel(script), tools=[fetch_attachment])


def large_replay():
    return H.build_agent(model=H.RefusingModel(), tools=[fetch_attachment])


def test_a_run_holding_a_payload_over_100kb_replays():
    """An oversized payload that cannot be read back is a run that cannot be
    replayed - discovered on the day unless it is tested here."""
    global LARGE_CALLS
    store = new_store()
    recorded = runs.record(store, large, H.PROMPT, run_id="large")
    assert LARGE_CALLS == 1

    LARGE_CALLS = 0
    replayed = runs.replay(store, "large", large_replay, H.PROMPT)

    assert LARGE_CALLS == 0
    assert replayed.answer == recorded.answer
    sizes = [len(e.result.value["content"][0]["text"]) for e in store.read("large")
             if e.type == "EffectCompleted" and isinstance(e.result.value, dict)]
    assert sizes == [len(BIG)]
