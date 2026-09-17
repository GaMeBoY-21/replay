"""The loaded run — an ordered event list plus the two indexes replay needs.

Building these while scanning costs one pass and a few hundred entries. It is
the reason the provenance feature needs no graph database: the reverse index
from a memory key to the event that wrote it is built as the log is read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from replay_events import (
    EffectCompleted,
    EffectRequested,
    Event,
    MemoryWrite,
    Result,
)

from .errors import ReplayExhausted


@dataclass
class EffectRecord:
    """One effect's two events, paired.

    `completed is None` is the crash signature: the request was appended, the
    effect was authorised, and no completion ever landed. The side effect may
    have happened. That question is the whole reason the gate is split, and it
    is answerable only because nothing ever wrote a placeholder completion.
    """

    seq: int
    requested: EffectRequested
    completed: EffectCompleted | None = None

    @property
    def effect(self):
        return self.requested.effect

    @property
    def result(self) -> Result:
        if self.completed is None:
            raise ReplayExhausted(
                f"effect {self.seq} was requested but never completed — the run died "
                f"mid-effect, and whether the side effect happened is unknown"
            )
        return self.completed.result


@dataclass
class RunLog:
    events: list[Event]
    by_seq: dict[int, EffectRecord] = field(default_factory=dict)
    writer_of: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for event in self.events:
            if isinstance(event, EffectRequested):
                self.by_seq[event.seq] = EffectRecord(seq=event.seq, requested=event)
            elif isinstance(event, EffectCompleted):
                record = self.by_seq.get(event.seq)
                if record is None:
                    raise ReplayExhausted(
                        f"effect {event.seq} completed without ever being requested"
                    )
                if record.completed is not None:
                    # One seq is never closed twice. A tool retry re-fires the
                    # SDK's before-call hook and opens a *new* effect with its
                    # own seq, which is correct; two completions at one seq is
                    # not, and means the gate was bypassed.
                    raise ReplayExhausted(f"effect {event.seq} was completed twice")
                record.completed = event
            elif isinstance(event, MemoryWrite):
                self.writer_of[event.key] = event.eid

    def at(self, seq: int) -> EffectRecord:
        try:
            return self.by_seq[seq]
        except KeyError:
            raise ReplayExhausted(
                f"replay asked for effect {seq}; the log holds {len(self.by_seq)}"
            ) from None

    @property
    def max_seq(self) -> int:
        return max(self.by_seq, default=-1)

    @property
    def next_eid(self) -> int:
        """The first free eid. A fork or a resume continues from here."""
        return max((e.eid for e in self.events), default=-1) + 1

    @property
    def incomplete(self) -> list[int]:
        return [seq for seq, rec in sorted(self.by_seq.items()) if rec.completed is None]


def load_log(store, run_id: str) -> RunLog:
    """Load one run's own events.

    Fork chain resolution — reading a parent's prefix and this run's own events
    from the fork point on — is not here yet, and this deliberately does not
    pretend to do it.
    """
    return RunLog(events=store.read(run_id))
