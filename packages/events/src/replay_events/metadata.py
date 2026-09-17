"""Run metadata — lineage, and the status that makes a crash legible.

Written *before* the first event, with `status="running"`, and updated after.
Writing lineage at the end instead leaves an orphan run on any failure: a fork
whose events exist but whose parent pointer never landed cannot be resolved, and
an unresolvable fork contradicts the prefix-sharing claim it depends on.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    TRIPPED = "tripped"
    FAILED = "failed"


class RunMetadata(BaseModel):
    run_id: str
    parent_run_id: str | None = None
    forked_at_seq: int | None = None
    mutated_event_id: int | None = None
    created_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    label: str | None = None
    # The parent's next free eid at the moment this run was created. A fork or a
    # resume continues the parent's eid sequence rather than restarting it;
    # restarting produces duplicate eids across a chain that is read as one log.
    eid_base: int = 0
    # The breaker ceilings this run was held to. A resume reads them back, so a
    # halted run carries the conditions of its own halt: the log is the run, and
    # a resume that needs the caller to remember the ceilings is not reproducible.
    breaker_config: dict[str, Any] | None = None

    @property
    def is_fork(self) -> bool:
        return self.parent_run_id is not None


class RunNotFound(KeyError):
    pass
