"""Run ids, monotonic within a process.

Random ids collided four times in five hundred. An id here is the wall clock in
milliseconds and a counter that increments whenever the clock has not moved on,
or has stepped backwards - so every id one process mints is distinct from the
last and sorts after it.

The clock read is not an input to any run: an id is minted before a run's first
event and never passes through the gate.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_last = [-1, 0]  # millisecond, counter of the most recent id


def new_run_id(prefix: str = "run") -> str:
    with _lock:
        now = time.time_ns() // 1_000_000
        if now <= _last[0]:
            now, counter = _last[0], _last[1] + 1
        else:
            counter = 0
        _last[:] = [now, counter]
    return f"{prefix}-{now:012x}-{counter:04x}"
