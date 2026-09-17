"""Chain resolution at depth, lineage under failure, fork seeding, resume, ids."""

from __future__ import annotations

import inspect
import json
import sys

import pytest

import strands_harness as H
from replay.agent import runs
from replay.ids import new_run_id
from replay.kernel import BreakerConfig, resolve
from replay.store import MemoryLogStore
from replay_events import (
    EffectCompleted,
    EffectRequested,
    MemoryRead,
    Result,
    RunMetadata,
    RunStatus,
    StepBoundary,
    ToolEffect,
)


def live():
    return H.build_agent()


# ---------------------------------------------------------------- depth


def build_chain(store, depth):
    """A root and `depth` forks, each owning one effect. Built directly on the
    store: this is about the resolver, not the agent."""
    for i in range(depth + 1):
        run_id, base = f"chain-{i}", 3 * i
        store.append(run_id, EffectRequested(eid=base, seq=i, effect=ToolEffect(name="step", arguments={"i": i})))
        store.append(run_id, EffectCompleted(eid=base + 1, seq=i, result=Result(value=i)))
        store.append(run_id, StepBoundary(eid=base + 2))
        store.put_metadata(RunMetadata(
            run_id=run_id,
            parent_run_id=None if i == 0 else f"chain-{i - 1}",
            forked_at_seq=None if i == 0 else i,
            eid_base=base,
            status=RunStatus.COMPLETED,
        ))


def test_a_500_deep_chain_resolves_iteratively():
    store = MemoryLogStore()
    build_chain(store, 500)

    # Leave headroom for the resolver's own frames and nothing like 500 of them:
    # a recursive resolver needs a frame per level and fails here.
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(len(inspect.stack()) + 60)
    try:
        log = resolve(store, "chain-500")
    finally:
        sys.setrecursionlimit(limit)

    assert sorted(log.by_seq) == list(range(501))
    eids = [e.eid for e in log.events]
    assert eids == list(range(3 * 501))
    assert [log.at(i).result.value for i in (0, 250, 500)] == [0, 250, 500]


def test_a_cycle_in_the_chain_is_refused():
    store = MemoryLogStore()
    store.put_metadata(RunMetadata(run_id="a", parent_run_id="b", forked_at_seq=1))
    store.put_metadata(RunMetadata(run_id="b", parent_run_id="a", forked_at_seq=1))
    with pytest.raises(ValueError, match="cycles"):
        resolve(store, "a")


# ---------------------------------------------------------------- lineage


class FailsWhenCalled(H.ScriptedModel):
    """Records what the store said about the fork at the moment the fork made a
    live call, then fails - a provider error mid-run."""

    seen: list = []

    def __init__(self, store, run_id):
        super().__init__()
        self.store, self.run_id = store, run_id

    async def stream(self, *args, **kwargs):
        FailsWhenCalled.seen.append(self.store.get_metadata(self.run_id))
        raise RuntimeError("provider connection reset")
        yield


def test_a_fork_that_fails_mid_run_still_resolves():
    store = MemoryLogStore()
    runs.record(store, live, H.PROMPT, run_id="root")
    FailsWhenCalled.seen.clear()
    mutation = Result(value={"toolUseId": "tooluse-2", "status": "success", "content": [{"text": "x"}]})

    with pytest.raises(Exception):
        runs.fork(store, "root", 2, mutation,
                  lambda: H.build_agent(model=FailsWhenCalled(store, "doomed")), H.PROMPT, run_id="doomed")

    (during,) = FailsWhenCalled.seen
    assert (during.parent_run_id, during.forked_at_seq, during.status) == ("root", 2, RunStatus.RUNNING), (
        "the fork had events and no lineage: an orphan"
    )
    after = store.get_metadata("doomed")
    assert (after.parent_run_id, after.status) == ("root", RunStatus.FAILED)

    log = resolve(store, "doomed")
    assert sorted(log.by_seq) == [0, 1, 2, 3], "prefix from root, 2 and 3 from the fork"
    assert log.incomplete == [3], "the call that failed is the crash signature"


# ---------------------------------------------------------------- seeding


def test_a_fork_starts_from_the_state_at_the_fork_point():
    """The parent wrote fx.rate at seq 2, the step the fork replaced. The fork
    must not see it - neither as a value nor as the writer of what it reads."""
    store = MemoryLogStore()
    runs.record(store, live, H.PROMPT, run_id="root")
    mutation = Result(value={"toolUseId": "tooluse-2", "status": "success", "content": [{"text": "x"}]})

    fork = runs.fork(store, "root", 2, mutation, live, H.PROMPT, run_id="fork")

    assert "fx.rate" not in H.raw_state(fork.agent)
    (read,) = [e for e in store.read("fork") if isinstance(e, MemoryRead) and e.key == "fx.rate"]
    assert read.source is None, "the fork's read points at a write its parent made after the fork point"


# ---------------------------------------------------------------- resume


LOOPS_THEN_ANSWERS = [
    H.tool_response(H.tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, f"loop-{n}"))
    for n in range(4)
] + [H.text_response("done")]


def looping():
    return H.build_agent(model=H.ScriptedModel(LOOPS_THEN_ANSWERS))


@pytest.fixture
def halted():
    from replay.kernel import Breakers

    store = MemoryLogStore()
    outcome = runs.record(store, looping, H.PROMPT, run_id="halted",
                          breakers=Breakers(BreakerConfig(max_repeats=3)))
    assert outcome.status == RunStatus.TRIPPED
    return store


def trip_seq(events):
    return next(e.seq for e in events if e.type == "BreakerTripped")


def test_resume_without_an_override_trips_again_at_the_same_seq(halted):
    store = halted
    H.reset_witnesses()

    again = runs.resume(store, "halted", looping, H.PROMPT,
                        breaker_config=BreakerConfig(max_repeats=3), new_run_id_="again")

    assert again.status == RunStatus.TRIPPED
    assert H.TOOL_EXECUTIONS == 0
    own = store.read("again")
    assert own[0].type == "BreakerTripped"
    assert own[0].seq == trip_seq(store.read("halted"))


def test_resume_with_an_override_continues(halted):
    store = halted
    H.reset_witnesses()

    resumed = runs.resume(store, "halted", looping, H.PROMPT,
                          breaker_config=BreakerConfig(max_repeats=3),
                          breaker_overrides={"max_repeats": 10}, new_run_id_="resumed")

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.answer.strip() == "done"
    assert H.TOOL_EXECUTIONS == 2, "the halted call and the one after it ran; the prefix did not"
    own = store.read("resumed")
    assert own[0].type == "EffectRequested" and own[0].seq == trip_seq(store.read("halted"))

    resolved = resolve(store, "resumed").events
    eids = [e.eid for e in resolved]
    assert eids == sorted(eids) and len(eids) == len(set(eids)), "resume restarted the eid sequence"


# ---------------------------------------------------------------- ids


def test_run_ids_are_monotonic_within_a_process():
    ids = [new_run_id() for _ in range(2000)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
