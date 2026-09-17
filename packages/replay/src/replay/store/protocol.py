"""The log store protocol.

Three backends implement this — in memory, SQLite and DynamoDB — and the whole
test suite runs against each of them unchanged. That is the proof of
equivalence: not a claim in a document, but the same assertions passing against
three different pieces of code.

**There is no mutate path, and adding one is not a small change.** The log is
append-only with no exceptions. When a value is not known at append time, the
answer is to split the write into two events, not to write a placeholder and
fill it in later — see `EffectRequested` for why that distinction carries real
information.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from replay_events import Event, RunMetadata


class EventIdConflict(Exception):
    """Two writers claimed the same `eid`.

    The append is conditional on the id being free, which is what makes
    "append-only" a guarantee rather than a convention.
    """


@runtime_checkable
class LogStore(Protocol):
    def append(self, run_id: str, event: Event) -> None:
        """Append one event. Raises `EventIdConflict` if its `eid` is taken."""
        ...

    def read(self, run_id: str) -> list[Event]:
        """Every event for this run, ordered by `eid`."""
        ...

    def put_metadata(self, metadata: RunMetadata) -> None:
        """Write or replace run metadata.

        Metadata is not part of the append-only log; it is lineage plus a status
        that moves `running` -> terminal exactly once.
        """
        ...

    def get_metadata(self, run_id: str) -> RunMetadata:
        """Raises `RunNotFound` if the run has no metadata."""
        ...

    def list_runs(self) -> list[RunMetadata]:
        ...
