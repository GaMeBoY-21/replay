"""The event union — the log, and therefore the run.

Ordering
--------
`eid` is monotonic over *every* event in a run and is the sole ordering key.
`seq` is the effect sequence and appears on effect events only.

Conflating them is a bug that bites on the first run: one effect appends two
events at the same `seq`, so a log keyed on `seq` fails its own append condition
on the second of them. Memory reads and writes, step boundaries and breaker
trips carry no `seq` at all and still need to interleave in order — which only
works if the ordering key is the one that counts every event.

`ts` is recorded, never regenerated. Nothing is appended during a replay, so a
clock read at append time only ever happens on a live step; it is display
metadata and is excluded from every comparison.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .effects import Effect, Result


UNSTAMPED = -1


class BaseEvent(BaseModel):
    # Stamped by `RunContext.append`, which is the only thing that may assign
    # one. A caller constructs events without an eid; the context refuses to
    # append an event that already carries one, so ordering cannot be forged.
    eid: int = UNSTAMPED
    ts: datetime | None = None


class EffectRequested(BaseEvent):
    """An effect is about to execute.

    Appended *before* execution. If the process dies between executing and
    recording, the log holds a request with no completion — and that asymmetry
    is the crash signature. It is the reason the gate is split into two halves
    rather than writing a placeholder completion up front: a null completion
    written before execution makes "died before the side effect" and "died after
    the side effect" indistinguishable, erasing the one question the signature
    exists to answer.
    """

    type: Literal["EffectRequested"] = "EffectRequested"
    seq: int
    effect: Effect
    reads: list[int] = Field(default_factory=list)


class EffectCompleted(BaseEvent):
    """What the effect produced. Appended exactly once per `seq`."""

    type: Literal["EffectCompleted"] = "EffectCompleted"
    seq: int
    result: Result
    substituted: bool = False


class MemoryRead(BaseEvent):
    """A read of agent state.

    `source` is the `eid` of the write that last produced this key, captured at
    read time from a reverse index built as the run proceeds. It is what makes
    the provenance chain walkable backwards without a graph database.
    """

    type: Literal["MemoryRead"] = "MemoryRead"
    key: str | None = None
    source: int | None = None


class MemoryWrite(BaseEvent):
    """A write to agent state.

    `value` is carried in the event because state is *rebuilt* from the log, not
    restored from a snapshot; without the value there is nothing to rebuild
    from. `reads` is the accumulated read-set of the step that produced this
    write — the edge the trace walks.
    """

    type: Literal["MemoryWrite"] = "MemoryWrite"
    key: str
    value: Any = None
    reads: list[int] = Field(default_factory=list)
    tombstone: bool = False


class StepBoundary(BaseEvent):
    """The end of one agent step.

    The read-set accumulator clears here and nowhere else, which fixes
    provenance granularity at one agent step: a write is attributed to
    everything the agent read during the step that produced it.
    """

    type: Literal["StepBoundary"] = "StepBoundary"
    label: str | None = None


class BreakerTripped(BaseEvent):
    """A breaker refused to let an effect run. The seq stays claimed."""

    type: Literal["BreakerTripped"] = "BreakerTripped"
    seq: int | None = None
    breaker: str
    detail: str = ""


class RunEnded(BaseEvent):
    type: Literal["RunEnded"] = "RunEnded"
    status: str
    detail: str | None = None


Event = Annotated[
    Union[
        EffectRequested,
        EffectCompleted,
        MemoryRead,
        MemoryWrite,
        StepBoundary,
        BreakerTripped,
        RunEnded,
    ],
    Field(discriminator="type"),
]

EventAdapter: TypeAdapter[Event] = TypeAdapter(Event)

EFFECT_EVENT_TYPES = ("EffectRequested", "EffectCompleted")


def parse_event(data: dict[str, Any]) -> Event:
    return EventAdapter.validate_python(data)


def dump_event(event: Event) -> dict[str, Any]:
    return EventAdapter.dump_python(event, mode="json")
