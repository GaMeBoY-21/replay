"""The trace and the step axis, on hand-built logs."""

from __future__ import annotations

import pytest

from replay.kernel import flag_output, step_index, step_to_seq, trace, trace_path, trace_view
from replay_events import (
    BreakerTripped,
    EffectCompleted,
    EffectRequested,
    MemoryRead,
    MemoryWrite,
    Result,
    StepBoundary,
    ToolEffect,
)


def stamp(events):
    for eid, event in enumerate(events):
        event.eid = eid
    return events


def effect(seq):
    return [EffectRequested(seq=seq, effect=ToolEffect(name=f"t{seq}")), EffectCompleted(seq=seq, result=Result())]


def diamond():
    """Step 1 writes a. Step 2 reads a, writes b. Step 3 reads a, writes c.
    Step 4 reads b and c, writes d. Step 5 reads d."""
    events = [*effect(0), MemoryWrite(key="a", value=1), StepBoundary()]              # 0-3
    events += [*effect(1), MemoryRead(key="a", source=2), StepBoundary()]              # 4-7
    events += [MemoryWrite(key="b", value=2, reads=[6])]                               # 8 (step 3)
    events += [*effect(2), MemoryRead(key="a", source=2), MemoryWrite(key="c", value=3, reads=[11]), StepBoundary()]  # 9-13
    events += [*effect(3), MemoryRead(key="b", source=8), MemoryRead(key="c", source=12),
               MemoryWrite(key="d", value=4, reads=[16, 17]), StepBoundary()]          # 14-19
    events += [*effect(4), MemoryRead(key="d", source=18), StepBoundary()]             # 20-23
    return stamp(events)


def test_a_step_ends_at_its_boundary():
    events = stamp([*effect(0), StepBoundary(), *effect(1), StepBoundary()])
    assert step_index(events) == {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 2}


def test_step_and_seq_are_different_axes():
    events = stamp([StepBoundary(), *effect(0), MemoryWrite(key="k"), StepBoundary(), *effect(1)])
    assert step_to_seq(events) == {2: 0, 3: 1}


def test_a_halted_step_is_named_by_its_trip():
    events = stamp([*effect(0), StepBoundary(), BreakerTripped(seq=1, breaker="loop"), StepBoundary()])
    assert step_to_seq(events) == {1: 0, 2: 1}


def test_the_trace_follows_every_branch_back_and_returns_the_whole_chain():
    events = diamond()
    flagged = flag_output(events)
    assert flagged == 22
    assert trace_path(events, flagged) == [2, 8, 12, 18]
    assert trace(events, flagged) == 2


def test_a_write_in_a_step_that_read_nothing_is_where_the_trace_stops():
    events = diamond()
    assert trace_path(events, 8) == [2, 8]


def test_the_trace_view_names_steps_keys_and_values():
    events = diamond()
    view = trace_view(events, flag_output(events))
    assert [(link["step"], link["key"], link["value"]) for link in view["chain"]] == [
        (1, "a", 1), (3, "b", 2), (3, "c", 3), (4, "d", 4)]
    assert view["head"]["key"] == "a"
    assert view["flagged"] == {"eid": 22, "step": 5, "key": "d"}


def test_a_run_that_read_nothing_has_no_output_to_trace():
    with pytest.raises(ValueError, match="nothing to trace"):
        flag_output(stamp([*effect(0), StepBoundary()]))
