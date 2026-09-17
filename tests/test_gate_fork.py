"""The gate: forks are themselves replayable, and a fork's own log starts at the
fork point.

The recorded run's effects are: model 0, read_invoice_header 1, convert_currency
2, model 3, record_total 4, model 5. The fork replaces convert_currency's result
at seq 2 - a TOOL step, so the substituted value reaches the next model call as a
tool result, where serving the parent's original would be visible.
"""

from __future__ import annotations

from backends import new_store
import json

import pytest

import strands_harness as H
from replay.agent import runs
from replay.kernel import resolve
from replay.store import MemoryLogStore
from replay_events import Result, canonical, normalise_messages

FORK_AT = 2
MUTATION = Result(value={
    "toolUseId": "tooluse-2",
    "status": "success",
    "content": [{"text": json.dumps({"converted": 492.0, "rate": 0.012, "warning": "source currency INR"})}],
})


def live():
    return H.build_agent()


def refusing():
    return H.build_agent(model=H.RefusingModel())


@pytest.fixture
def forked():
    store = new_store()
    root = runs.record(store, live, H.PROMPT, run_id="root")
    fork = runs.fork(store, "root", FORK_AT, MUTATION, live, H.PROMPT, run_id="fork-a")
    return store, root, fork


def requested(events):
    return [e for e in events if e.type == "EffectRequested"]


def tool_result_text(agent, tool_use_id):
    for message in agent.messages:
        for block in message["content"]:
            result = block.get("toolResult")
            if result and result["toolUseId"] == tool_use_id:
                return result["content"][0]["text"]
    raise LookupError(tool_use_id)


# ---------------------------------------------------------------- the gate


def test_replaying_a_fork_reproduces_the_fork(forked):
    """Including the SUBSTITUTED value at the fork point, not the parent's
    original. A boundary one step late fails exactly here."""
    store, root, fork = forked
    H.reset_witnesses()

    replayed = runs.replay(store, "fork-a", refusing, H.PROMPT)

    assert H.TOOL_EXECUTIONS == 0 and H.MODEL_CALLS == 0, "replaying a fork executed something"
    assert replayed.answer == fork.answer
    assert canonical(normalise_messages(replayed.agent.messages)) == canonical(
        normalise_messages(fork.agent.messages)
    )
    assert canonical(H.raw_state(replayed.agent)) == canonical(H.raw_state(fork.agent))

    served = tool_result_text(replayed.agent, "tooluse-2")
    assert served == MUTATION.value["content"][0]["text"]
    assert served != tool_result_text(root.agent, "tooluse-2"), "the fork served its parent's value"


def test_a_forks_own_log_starts_at_the_fork_point(forked):
    store, root, fork = forked
    metadata = store.get_metadata("fork-a")
    own = store.read("fork-a")

    assert own[0].type == "EffectRequested" and own[0].seq == FORK_AT, (
        f"the fork's own log begins with {own[0].type} at seq {getattr(own[0], 'seq', None)}"
    )
    assert own[1].type == "EffectCompleted" and own[1].substituted
    assert own[1].eid == metadata.mutated_event_id
    assert all(getattr(e, "seq", FORK_AT) >= FORK_AT for e in own), "a prefix event is in the fork's own log"
    assert min(e.eid for e in own) == metadata.eid_base == resolve(store, "root").next_eid


# ---------------------------------------------------------------- the boundary


def test_a_fork_is_identical_to_its_parent_before_the_fork_point(forked):
    store, _, _ = forked
    parent = requested(resolve(store, "root").events)
    child = resolve(store, "fork-a")

    for event in requested(child.events):
        if event.seq < FORK_AT:
            original = next(p for p in parent if p.seq == event.seq)
            assert event.effect.shape() == original.effect.shape()
            assert canonical(child.at(event.seq).result.model_dump()) == canonical(
                resolve(store, "root").at(event.seq).result.model_dump()
            )


def test_each_seq_is_requested_exactly_once_in_the_resolved_fork(forked):
    """If the parent's request at the fork point leaked into the prefix, the
    resolved log would ask for that step twice."""
    store, _, _ = forked
    seqs = [e.seq for e in requested(resolve(store, "fork-a").events)]
    assert seqs == list(range(len(seqs)))


def test_a_fork_stores_its_divergence_not_the_run(forked):
    store, _, _ = forked
    own = store.read("fork-a")
    resolved = resolve(store, "fork-a").events
    cut = own[0].eid

    assert len(own) == len([e for e in resolved if e.eid >= cut])
    assert len(own) < len(resolved)

    # The prefix is read from the root: exactly its events before its own
    # request at the fork point.
    root = store.read("root")
    root_cut = next(e.eid for e in root if getattr(e, "seq", None) == FORK_AT)
    assert [e.eid for e in resolved if e.eid < cut] == [e.eid for e in root if e.eid < root_cut]


def test_the_resolved_fork_is_one_ordered_log(forked):
    store, _, _ = forked
    eids = [e.eid for e in resolve(store, "fork-a").events]
    assert eids == sorted(eids) and len(eids) == len(set(eids))


# ---------------------------------------------------------------- forks of forks


def test_a_fork_of_a_fork_resolves_and_replays(forked):
    store, _, fork_a = forked
    second = Result(value={"toolUseId": "tooluse-3", "status": "success", "content": [{"text": "overridden"}]})
    fork_b = runs.fork(store, "fork-a", 4, second, live, H.PROMPT, run_id="fork-b")

    resolved = resolve(store, "fork-b")
    eids = [e.eid for e in resolved.events]
    assert eids == sorted(eids) and len(eids) == len(set(eids)), "eids across the chain collide"
    seqs = [e.seq for e in requested(resolved.events)]
    assert seqs == list(range(len(seqs)))

    substituted = [e.seq for e in resolved.events if e.type == "EffectCompleted" and e.substituted]
    assert substituted == [FORK_AT, 4], "both mutations are in the resolved chain"
    assert store.read("fork-b")[0].seq == 4

    H.reset_witnesses()
    replayed = runs.replay(store, "fork-b", refusing, H.PROMPT)
    assert H.TOOL_EXECUTIONS == 0
    assert replayed.answer == fork_b.answer
    assert tool_result_text(replayed.agent, "tooluse-3") == "overridden"
