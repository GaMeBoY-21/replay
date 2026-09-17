"""Agent state, recorded.

`reads` and `writes` look like bookkeeping and are in fact the entire provenance
feature. They are captured at the same gate as everything else and nowhere else:
an un-gated access leaves a hole in the graph, and the trace then returns a
confidently wrong origin, which is the worst possible failure for a debugging
tool.
"""

from __future__ import annotations

from typing import Any

from replay_events import MemoryRead, MemoryWrite

from .context import RunContext


class DictState:
    """The minimal state substrate, for tests and for the local runner."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    def get(self, key: str | None = None) -> Any:
        if key is None:
            return dict(self._data)
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class RecordingState:
    """Wraps a state object. Every read and write becomes an event."""

    def __init__(self, ctx: RunContext, inner) -> None:
        self._ctx = ctx
        self._inner = inner

    def get(self, key: str | None = None) -> Any:
        eid = self._ctx.append(
            MemoryRead(key=key, source=self._ctx.writer_of.get(key) if key else None)
        )
        self._ctx.pending_reads.append(eid)
        return self._inner.get(key)

    def set(self, key: str, value: Any) -> None:
        eid = self._ctx.append(
            MemoryWrite(key=key, value=value, reads=self._ctx.snapshot_reads())
        )
        self._ctx.writer_of[key] = eid
        self._inner.set(key, value)

    def delete(self, key: str) -> None:
        eid = self._ctx.append(
            MemoryWrite(key=key, tombstone=True, reads=self._ctx.snapshot_reads())
        )
        self._ctx.writer_of[key] = eid
        self._inner.delete(key)


def state_at(events, eid: int | None = None) -> dict[str, Any]:
    """Rebuild agent state from the log, up to and including `eid`.

    State is *rebuilt*, never restored from a snapshot — which is why the write
    events carry their values.

    Do not use this to seed a replay before it starts. A step that reads a key
    and then writes it would see a value its own step has not yet written, and
    the run diverges on its first read-then-write. Seeding belongs one layer up,
    in the framework integration, where tools are stubbed and genuinely do not
    execute. Where writes re-execute, the run reconstructs its own state as it
    goes.
    """
    state: dict[str, Any] = {}
    for event in events:
        if not isinstance(event, MemoryWrite):
            continue
        if eid is not None and event.eid > eid:
            break
        if event.tombstone:
            state.pop(event.key, None)
        else:
            state[event.key] = event.value
    return state
