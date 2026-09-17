"""The run context — identity, mode, counters, and the only append path."""

from __future__ import annotations

from datetime import datetime, timezone

from replay_events import UNSTAMPED, Event, StepBoundary

from .breakers import Breakers
from .log import RunLog
from .modes import LiveMode, Mode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunContext:
    """Everything one run needs, and nothing a second run could share.

    Two counters, and they are not the same counter:

    - `eid` is monotonic over *every* event and is the sole ordering key.
    - `seq` is the effect index — what replay addresses, what the fork API takes,
      and the axis a diff aligns on.

    One effect appends two events, so a log keyed on `seq` would fail its own
    append condition on the second of them.
    """

    def __init__(
        self,
        run_id: str,
        store,
        mode: Mode | None = None,
        log: RunLog | None = None,
        breakers: Breakers | None = None,
        eid_base: int = 0,
        clock=_utc_now,
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.mode: Mode = mode or LiveMode()
        self.log = log
        self.breakers = breakers or Breakers()
        self._eid = eid_base
        self._seq = 0
        # The accumulating read-set of the current agent step.
        self.pending_reads: list[int] = []
        # memory key -> eid of the write that last produced it.
        self.writer_of: dict[str, int] = dict(log.writer_of) if log else {}
        self._clock = clock

    # ---- counters ----

    def next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def reserve(self, count: int) -> int:
        """Claim a block of eids and return the first.

        A fork and a resume both continue the parent's eid sequence rather than
        restarting it. Restarting produces duplicate eids across a chain that is
        later read as a single ordered log, and the duplicates are invisible
        until something sorts by eid and silently drops half the run.
        """
        base = self._eid
        self._eid += count
        return base

    # ---- the only append path ----

    def append(self, event: Event) -> int:
        """Stamp an event with its eid and timestamp, and store it.

        `ts` is recorded here and never regenerated. Nothing is appended during a
        replay, so this clock read only ever happens on a live step; the value is
        display metadata and takes part in no comparison.
        """
        if event.eid != UNSTAMPED:
            raise ValueError(
                f"event arrived already stamped with eid {event.eid}; "
                "only RunContext.append may assign one"
            )
        event.eid = self.reserve(1)
        if event.ts is None:
            event.ts = self._clock()
        self.store.append(self.run_id, event)
        return event.eid

    # ---- the read-set accumulator ----

    def snapshot_reads(self) -> list[int]:
        """A copy of the pending read-set. Never clears it.

        Clearing happens at a step boundary and nowhere else, which fixes
        provenance granularity at one agent step: a write is attributed to
        everything the agent read during the step that produced it. Finer
        granularity would mean data-flow analysis inside the model.
        """
        return list(self.pending_reads)

    def step_boundary(self, label: str | None = None) -> int:
        eid = self.append(StepBoundary(label=label))
        self.pending_reads.clear()
        return eid
