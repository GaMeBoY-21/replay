"""The Replay event contract.

This package is the single schema source: the kernel imports it, and the
frontend's types are generated from it. Nothing else belongs here — no storage,
no kernel logic, no AWS. Guard that boundary; it is the reason the stack works.
"""

from .canonical import canonical
from .effects import (
    VOLATILE_MESSAGE_FIELDS,
    ClockEffect,
    Effect,
    ErrorInfo,
    ModelEffect,
    RandomEffect,
    Result,
    ToolEffect,
    normalise_messages,
)
from .events import (
    EFFECT_EVENT_TYPES,
    UNSTAMPED,
    BreakerTripped,
    EffectCompleted,
    EffectRequested,
    Event,
    EventAdapter,
    MemoryRead,
    MemoryWrite,
    RunEnded,
    StepBoundary,
    dump_event,
    parse_event,
)
from .metadata import RunMetadata, RunNotFound, RunStatus

__all__ = [
    "EFFECT_EVENT_TYPES",
    "UNSTAMPED",
    "VOLATILE_MESSAGE_FIELDS",
    "BreakerTripped",
    "ClockEffect",
    "Effect",
    "EffectCompleted",
    "EffectRequested",
    "ErrorInfo",
    "Event",
    "EventAdapter",
    "MemoryRead",
    "MemoryWrite",
    "ModelEffect",
    "RandomEffect",
    "Result",
    "RunEnded",
    "RunMetadata",
    "RunNotFound",
    "RunStatus",
    "StepBoundary",
    "ToolEffect",
    "canonical",
    "dump_event",
    "normalise_messages",
    "parse_event",
]
