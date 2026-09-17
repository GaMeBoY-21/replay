"""The in-memory log store.

The reference implementation of the protocol, and the one the property test
hammers. It enforces the same conditional append as DynamoDB: a store that
silently accepted a duplicate `eid` would let an ordering bug through here and
surface it only once deployed.
"""

from __future__ import annotations

from replay_events import Event, RunMetadata, RunNotFound

from .protocol import EventIdConflict


class MemoryLogStore:
    def __init__(self) -> None:
        self._events: dict[str, dict[int, Event]] = {}
        self._metadata: dict[str, RunMetadata] = {}

    def append(self, run_id: str, event: Event) -> None:
        run = self._events.setdefault(run_id, {})
        if event.eid in run:
            raise EventIdConflict(f"{run_id}: eid {event.eid} is already taken")
        run[event.eid] = event

    def read(self, run_id: str) -> list[Event]:
        return [self._events.get(run_id, {})[k] for k in sorted(self._events.get(run_id, {}))]

    def put_metadata(self, metadata: RunMetadata) -> None:
        self._metadata[metadata.run_id] = metadata

    def get_metadata(self, run_id: str) -> RunMetadata:
        try:
            return self._metadata[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    def list_runs(self) -> list[RunMetadata]:
        # Run ids are monotonic within a process, so ordering by id is both
        # stable and chronological. Sorting on `created_at` would mix None with
        # datetimes and raise on the first run that has no timestamp yet.
        return sorted(self._metadata.values(), key=lambda m: m.run_id)
