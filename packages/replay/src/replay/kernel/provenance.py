"""Steps, and the provenance trace.

Two numbering axes, and they are not the same:

- A **step** is what a person sees. A `StepBoundary` ends one, so the step of an
  event is one more than the number of boundaries before it.
- A **seq** is an effect's index: what replay addresses and the fork API takes.

They differ because reads, writes and boundaries take eids without consuming a
seq. `step_to_seq` is the only correct bridge. Forking "step 12" by passing 12 as
a seq forks the wrong effect, and it looks like a kernel bug when it is an
off-by-a-different-axis in the caller.

The trace walks backwards from a flagged event: a read to the write it saw
(`MemoryRead.source`), a write to everything its step read (`MemoryWrite.reads`).
It returns **the whole chain**, earliest first, not just the head. "Earliest
reachable write" alone degenerates towards the run's origin in a long run,
because everything transitively depends on what the agent did first; the chain
is what shows how an error reached the output, and it is honest about what the
walk knows.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from replay_events import BreakerTripped, EffectRequested, Event, MemoryRead, MemoryWrite, StepBoundary


def step_index(events: list[Event]) -> dict[int, int]:
    """eid -> the step it belongs to, counting from 1."""
    steps: dict[int, int] = {}
    step = 1
    for event in events:
        steps[event.eid] = step
        if isinstance(event, StepBoundary):
            step += 1
    return steps


def step_to_seq(events: list[Event]) -> dict[int, int]:
    """step -> the seq of the effect performed at that step.

    A halted step has no request - the breaker stopped it first - but its trip
    claimed the seq, so the trip names the step.
    """
    steps = step_index(events)
    mapping: dict[int, int] = {}
    for event in events:
        if isinstance(event, (EffectRequested, BreakerTripped)) and event.seq is not None:
            mapping.setdefault(steps[event.eid], event.seq)
    return mapping


def trace_path(events: list[Event], flagged_eid: int) -> list[int]:
    """Every write the flagged event depends on, earliest first.

    Breadth-first and iterative. A step's writes carry the read-set of the step
    that produced them, and a read carries the eid of the write it saw, so the
    walk needs nothing but the log.
    """
    by_eid = {event.eid: event for event in events}
    if flagged_eid not in by_eid:
        raise KeyError(f"event {flagged_eid} is not in this log")
    frontier = deque([flagged_eid])
    seen: set[int] = set()
    writes: set[int] = set()
    while frontier:
        eid = frontier.popleft()
        if eid in seen or eid not in by_eid:
            continue
        seen.add(eid)
        event = by_eid[eid]
        if isinstance(event, MemoryRead):
            if event.source is not None:
                frontier.append(event.source)
        elif isinstance(event, MemoryWrite):
            writes.add(eid)
            frontier.extend(event.reads)
    return sorted(writes)


def trace(events: list[Event], flagged_eid: int) -> int | None:
    """The head of the chain: the earliest write the flagged event depends on."""
    path = trace_path(events, flagged_eid)
    return path[0] if path else None


def flag_output(events: list[Event]) -> int:
    """The event to trace a run's output from: the last read of agent state.

    A model's final answer does not read state - only tools do - so what an
    output was built from is the last thing the run read before producing it.
    """
    reads = [event.eid for event in events if isinstance(event, MemoryRead)]
    if not reads:
        raise ValueError("this run read no state, so there is nothing to trace its output from")
    return reads[-1]


def trace_view(events: list[Event], flagged_eid: int) -> dict[str, Any]:
    """The chain as a person reads it: steps, keys and values, earliest first."""
    by_eid = {event.eid: event for event in events}
    steps = step_index(events)
    flagged = by_eid[flagged_eid]
    chain = [
        {"eid": eid, "step": steps[eid], "key": by_eid[eid].key, "value": by_eid[eid].value}
        for eid in trace_path(events, flagged_eid)
    ]
    effect_steps = [steps[e.eid] for e in events if isinstance(e, (EffectRequested, BreakerTripped))]
    return {
        "flagged": {"eid": flagged_eid, "step": steps[flagged_eid], "key": getattr(flagged, "key", None)},
        "output_step": max(effect_steps, default=None),
        "chain": chain,
        "head": chain[0] if chain else None,
    }
