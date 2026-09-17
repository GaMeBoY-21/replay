"""Invariants the kernel would otherwise break silently.

Each of these guards a sentence in the architecture. If the sentence stops being
true, one of these goes red.
"""

from __future__ import annotations

import pytest

from harness import Step, effect_for, record, replay
from replay.kernel import (
    Begun,
    DictState,
    DivergenceError,
    LiveMode,
    RecordingState,
    ReplayExhausted,
    ReplayMode,
    RunContext,
    assert_same_shape,
    begin_effect,
    complete_effect,
    load_log,
    perform,
    state_at,
)
from replay.store import EventIdConflict, MemoryLogStore
from replay_events import (
    EffectCompleted,
    ModelEffect,
    RandomEffect,
    Result,
    StepBoundary,
    ToolEffect,
)

SCRIPT = [
    Step(kind="tool", name="search", args={"q": "a"}, writes="hits"),
    Step(kind="model", name="summarise", reads=("hits",), writes="summary"),
    Step(kind="tool", name="fetch", args={"id": 1}, reads=("summary",), writes="doc"),
]


# ---------------------------------------------------------------- divergence


def test_assert_same_shape_does_not_fire_on_a_correct_replay():
    """CLAIM: the detector is silent when nothing is wrong.

    This is the half that is usually missing. A detector that fires on a correct
    replay is worse than no detector, because the first thing anyone does is
    disable it — and §18 depends on it never being disabled.
    """
    recorded = record(SCRIPT)
    replay(recorded, SCRIPT)  # no exception


def test_assert_same_shape_catches_a_changed_tool_argument():
    """CLAIM: the detector is the only thing standing between a drifted
    recording and a plausible wrong answer."""
    recorded = record(SCRIPT)
    drifted = list(SCRIPT)
    drifted[0] = Step(kind="tool", name="search", args={"q": "b"}, writes="hits")
    with pytest.raises(DivergenceError):
        replay(recorded, drifted)


def test_assert_same_shape_catches_a_changed_effect_kind():
    recorded = record(SCRIPT)
    drifted = list(SCRIPT)
    drifted[0] = Step(kind="random", name="uuid4", writes="hits")
    with pytest.raises(DivergenceError):
        replay(recorded, drifted)


def test_assert_same_shape_catches_tool_choice_drift():
    """CLAIM: the fingerprint covers five inputs, not three.

    A fingerprint of messages + tool_specs + system_prompt passes here, and
    passing here means serving the wrong recorded response.
    """
    a = ModelEffect(messages=[{"role": "user", "content": "hi"}])
    b = ModelEffect(messages=[{"role": "user", "content": "hi"}], tool_choice={"auto": {}})
    with pytest.raises(DivergenceError):
        assert_same_shape(a, b)


def test_assert_same_shape_catches_system_prompt_content_drift():
    a = ModelEffect(messages=[{"role": "user", "content": "hi"}])
    b = ModelEffect(
        messages=[{"role": "user", "content": "hi"}], system_prompt_content=[{"text": "x"}]
    )
    with pytest.raises(DivergenceError):
        assert_same_shape(a, b)


def test_volatile_message_fields_do_not_cause_a_false_divergence():
    """CLAIM: dropping tracking_id and metadata is not a fudge.

    Two identical runs differ on exactly those fields, because the SDK mints a
    fresh uuid4 per run and strips it before the model sees it. Without the
    normalisation the most important test in the project fails on day one for a
    reason that has nothing to do with replay.
    """
    a = ModelEffect(messages=[{"role": "user", "content": "hi", "tracking_id": "one"}])
    b = ModelEffect(messages=[{"role": "user", "content": "hi", "tracking_id": "two"}])
    assert_same_shape(a, b)  # no exception


def test_tool_use_id_is_outside_the_fingerprint():
    """CLAIM: the SDK builds tool-use ids with random.randint on one path.

    Inside the fingerprint, every replay diverges.
    """
    a = ToolEffect(name="f", arguments={"x": 1}, tool_use_id="tooluse_aaa")
    b = ToolEffect(name="f", arguments={"x": 1}, tool_use_id="tooluse_bbb")
    assert_same_shape(a, b)  # no exception


def test_argument_key_order_is_not_a_divergence():
    """CLAIM: dict keys are sorted before hashing or comparing."""
    a = ToolEffect(name="f", arguments={"a": 1, "b": 2})
    b = ToolEffect(name="f", arguments={"b": 2, "a": 1})
    assert_same_shape(a, b)  # no exception


# ---------------------------------------------------------- the payload trap


def test_a_recorded_none_result_is_served_not_re_executed():
    """CLAIM: Begun.live is the flag, never `result is None`.

    A recorded result is allowed to be None. Testing the payload instead of the
    flag re-executes the effect — during a replay, which is the one thing replay
    must never do.
    """
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store, mode=LiveMode())
    effect = ToolEffect(name="returns_nothing", arguments={})
    perform(ctx, effect, lambda: Result(value=None))

    log = load_log(store, "r")
    replay_ctx = RunContext(
        run_id="r2", store=MemoryLogStore(), mode=ReplayMode(up_to=log.max_seq), log=log
    )

    def must_not_run():
        raise AssertionError("re-executed an effect whose recorded result was None")

    result = perform(replay_ctx, effect, must_not_run)
    assert result.value is None


# ------------------------------------------------------------- append-only


def test_the_store_refuses_a_duplicate_eid():
    """CLAIM: append is conditional, so append-only is a guarantee rather than
    a convention."""
    store = MemoryLogStore()
    store.append("r", StepBoundary(eid=0))
    with pytest.raises(EventIdConflict):
        store.append("r", StepBoundary(eid=0))


def test_the_context_refuses_a_pre_stamped_event():
    """CLAIM: only RunContext.append assigns an eid, so ordering cannot be
    forged by a caller."""
    ctx = RunContext(run_id="r", store=MemoryLogStore())
    with pytest.raises(ValueError):
        ctx.append(StepBoundary(eid=7))


def test_the_store_has_no_mutate_path():
    """CLAIM: the log is append-only with no exceptions.

    Guarded structurally rather than by review: a backend that grows an update
    method fails here.
    """
    banned = {"update", "set", "replace", "edit", "patch", "delete", "amend"}
    exposed = {name for name in dir(MemoryLogStore) if not name.startswith("_")}
    assert not (exposed & banned), f"the store grew a mutate path: {exposed & banned}"


def test_an_effect_cannot_be_completed_twice():
    """CLAIM: one seq is never closed twice.

    A tool retry re-fires the SDK's before-call hook and opens a new effect with
    its own seq, which is correct. Two completions at one seq means the gate was
    bypassed.
    """
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    begun = begin_effect(ctx, ToolEffect(name="f", arguments={}))
    complete_effect(ctx, begun.seq, Result(value=1))
    complete_effect(ctx, begun.seq, Result(value=2))
    with pytest.raises(ReplayExhausted):
        load_log(store, "r")


# --------------------------------------------------- the crash signature


def test_a_request_without_a_completion_is_the_crash_signature():
    """CLAIM: the gate is split so that dying mid-effect stays legible.

    A placeholder completion written before execution would make "died before
    the side effect" and "died after it" indistinguishable — erasing the one
    question the signature exists to answer.
    """
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    begin_effect(ctx, ToolEffect(name="charge_card", arguments={"amount": 10}))
    # process dies here

    log = load_log(store, "r")
    assert log.incomplete == [0]
    with pytest.raises(ReplayExhausted):
        log.at(0).result


# ------------------------------------------------------- the read-set


def test_the_read_set_clears_only_at_a_step_boundary():
    """CLAIM: provenance granularity is one agent step.

    A write is attributed to everything the agent read during the step that
    produced it. If the set cleared per-read, the chain breaks; if it never
    cleared, every write would claim every earlier read.
    """
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    state = RecordingState(ctx, DictState())

    state.get("a")
    state.get("b")
    state.set("c", 1)
    ctx.step_boundary()
    state.set("d", 2)

    writes = {e.key: e for e in store.read("r") if e.type == "MemoryWrite"}
    assert len(writes["c"].reads) == 2, "the write should carry both reads of its step"
    assert writes["d"].reads == [], "the read-set must clear at the boundary"


def test_snapshot_reads_copies_and_does_not_clear():
    ctx = RunContext(run_id="r", store=MemoryLogStore())
    ctx.pending_reads.extend([1, 2])
    snapshot = ctx.snapshot_reads()
    snapshot.append(99)
    assert ctx.pending_reads == [1, 2]


def test_a_read_records_the_write_that_produced_it():
    """CLAIM: the reverse index is built as the run proceeds, which is what
    makes the trace walkable without a graph database."""
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    state = RecordingState(ctx, DictState())

    state.set("k", "poisoned")
    write_eid = ctx.writer_of["k"]
    state.get("k")

    read = next(e for e in store.read("r") if e.type == "MemoryRead")
    assert read.source == write_eid


# --------------------------------------------------------------- state_at


def test_state_is_rebuilt_from_the_log():
    """CLAIM: replayed state is rebuilt, not restored."""
    recorded = record(SCRIPT)
    events = recorded.store.read(recorded.run_id)
    assert state_at(events) == recorded.state


def test_state_at_honours_tombstones():
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    state = RecordingState(ctx, DictState())
    state.set("k", 1)
    state.delete("k")
    assert state_at(store.read("r")) == {}


def test_state_at_is_a_prefix_view():
    store = MemoryLogStore()
    ctx = RunContext(run_id="r", store=store)
    state = RecordingState(ctx, DictState())
    state.set("k", "before")
    cut = ctx.writer_of["k"]
    state.set("k", "after")
    assert state_at(store.read("r"), eid=cut) == {"k": "before"}
    assert state_at(store.read("r")) == {"k": "after"}


# ------------------------------------------------------------- exhaustion


def test_replay_past_the_end_of_the_log_fails_loudly():
    recorded = record(SCRIPT)
    log = load_log(recorded.store, recorded.run_id)
    ctx = RunContext(
        run_id="x", store=MemoryLogStore(), mode=ReplayMode(up_to=log.max_seq + 5), log=log
    )
    for step in SCRIPT:
        perform(ctx, effect_for(step), lambda: Result())
    with pytest.raises(ReplayExhausted):
        perform(ctx, ToolEffect(name="one_too_many", arguments={}), lambda: Result())
