"""The stage 1 gate.

Two claims, and the stage is not done until both are green:

1. A ten-step run records and replays byte-identically.
2. `execute()` is never called during replay — proved with a poisoned executor
   that raises, not with a return value that could be coincidentally equal.
"""

from __future__ import annotations

import pytest

from harness import (
    ExecutedDuringReplay,
    Step,
    poisoned_executor,
    record,
    replay,
    run_script,
)
from replay.kernel import load_log
from replay.store import MemoryLogStore
from replay_events import canonical

TEN_STEPS = [
    Step(kind="tool", name="search", args={"q": "invoice"}, writes="hits"),
    Step(kind="model", name="summarise", reads=("hits",), writes="summary"),
    Step(kind="clock", name="now", writes="t0"),
    Step(kind="tool", name="fetch", args={"id": 1}, reads=("summary",), writes="doc"),
    Step(kind="random", name="uuid4", writes="ref"),
    Step(kind="tool", name="score", args={"doc": "x"}, reads=("doc", "ref"), writes="score"),
    Step(kind="model", name="decide", reads=("score",), writes="decision"),
    Step(kind="tool", name="write_back", args={"v": 2}, reads=("decision",), writes="ack"),
    Step(kind="clock", name="now", writes="t1"),
    Step(kind="model", name="answer", reads=("ack", "t1"), writes="answer"),
]


def test_a_ten_step_run_replays_byte_identically():
    """GATE: replaying a recorded run reproduces identical state.

    Byte-identical is checked on the canonical rendering, not on Python equality,
    so a difference in key order or numeric type is a failure rather than a
    coincidence.
    """
    recorded = record(TEN_STEPS)
    assert len(recorded.state) == 10, "the script should have written ten keys"

    replayed = replay(recorded, TEN_STEPS)

    assert canonical(replayed.state) == canonical(recorded.state)
    assert canonical(replayed.served) == canonical(recorded.served)


def test_execute_is_never_called_during_replay():
    """GATE: replay serves from the log and runs nothing.

    The executor raises on any call. A replay that reached it would fail here
    rather than quietly returning a value that happened to match.
    """
    recorded = record(TEN_STEPS)
    replayed = replay(recorded, TEN_STEPS, executor=poisoned_executor)
    assert canonical(replayed.state) == canonical(recorded.state)


def test_the_poison_is_live_ammunition():
    """The previous test only means something if the poison actually fires.

    A test whose guard cannot fail proves nothing. This runs the same poisoned
    executor in LIVE mode and requires the explosion, which is what makes the
    replay test's silence informative.
    """
    with pytest.raises(ExecutedDuringReplay):
        run_script(
            TEN_STEPS, run_id="poison-check", store=MemoryLogStore(), executor=poisoned_executor
        )


def test_replay_appends_nothing_to_the_recorded_log():
    """A replay must not grow the run it is replaying."""
    recorded = record(TEN_STEPS)
    before = len(recorded.store.read(recorded.run_id))
    replay(recorded, TEN_STEPS)
    after = len(recorded.store.read(recorded.run_id))
    assert before == after


def test_every_effect_in_the_recorded_log_is_closed_exactly_once():
    """A completed run leaves no effect requested-but-not-completed.

    The asymmetry is the crash signature, so a clean run must not show it.
    """
    recorded = record(TEN_STEPS)
    log = load_log(recorded.store, recorded.run_id)
    assert log.incomplete == []
    assert log.max_seq == len(TEN_STEPS) - 1
