"""Fork chain resolution.

A fork does not copy its parent's events. Reading run `R` means the parent's
events for seq `0 … forked_at_seq - 1`, then `R`'s own events from
`forked_at_seq` onward.

**The forked step belongs to the fork.** The fork replaced that step's result,
so the fork owns both of its events. Putting the boundary one step later - the
obvious choice - resolves the fork to the parent's original value at exactly the
step that was mutated, and replaying the fork then serves a value it never saw.

The boundary is an eid, not a seq. Step boundaries and memory reads and writes
carry no seq, and filtering by seq alone would let the parent's bookkeeping from
after the fork point leak into the child. So the prefix is every parent event
before the first event that carries the fork's seq - its request, or, for a
resume, the breaker trip that halted it.
"""

from __future__ import annotations

from replay_events import Event, RunMetadata, RunNotFound

from .errors import UnresolvableRun
from .log import RunLog


def first_eid_at(events: list[Event], seq: int) -> int | None:
    """The eid of the first event carrying this seq, or None if none does."""
    for event in events:
        if getattr(event, "seq", None) == seq:
            return event.eid
    return None


def lineage(store, run_id: str) -> list[tuple[str, RunMetadata]]:
    """The chain from this run up to its root, leaf first.

    Every run in the chain must have metadata. A run without it cannot be told
    apart from a root, and resolving an orphaned fork as one returns a shorter,
    wrong, plausible log with its prefix missing. Lineage-first makes that rare;
    raising here makes it loud.
    """
    chain: list[tuple[str, RunMetadata]] = []
    seen: set[str] = set()
    current: str | None = run_id
    # A loop, never recursion. A recursive resolver on a deep chain previously
    # drove two Python processes to ~35 GB each on a 24 GB machine.
    while current is not None:
        if current in seen:
            names = " -> ".join(name for name, _ in chain)
            raise ValueError(f"the fork chain cycles back to {current!r}: {names} -> {current}")
        seen.add(current)
        try:
            metadata = store.get_metadata(current)
        except RunNotFound:
            asked = "" if current == run_id else f" (reached from {run_id})"
            raise UnresolvableRun(
                f"{current}{asked} has no metadata, so its parent is unknown and its log "
                "cannot be resolved"
            ) from None
        chain.append((current, metadata))
        current = metadata.parent_run_id
    return chain


def resolve(store, run_id: str) -> RunLog:
    """The run as one ordered log: its ancestors' prefixes, spliced."""
    chain = lineage(store, run_id)
    root, _ = chain[-1]
    events: list[Event] = store.read(root)
    for child, metadata in reversed(chain[:-1]):
        cut = first_eid_at(events, metadata.forked_at_seq)
        prefix = events if cut is None else [e for e in events if e.eid < cut]
        events = prefix + store.read(child)
    return RunLog(events=events)
