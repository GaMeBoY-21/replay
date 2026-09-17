"""The SQLite log store. No account, no network; the local runner's backend.

    PK RUN#{run_id} / SK EVT#{eid:09d}   ->   PRIMARY KEY (run_id, eid)
    attribute_not_exists(SK)             ->   a plain INSERT, which fails on the key
    Query, ScanIndexForward=True         ->   ORDER BY eid
"""

from __future__ import annotations

import os
import sqlite3
import threading

from replay_events import Event, RunMetadata, RunNotFound

from .codec import decode_event, decode_metadata, encode_event, encode_metadata
from .protocol import EventIdConflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    eid INTEGER NOT NULL,
    type TEXT NOT NULL,
    seq INTEGER,
    body TEXT NOT NULL,
    PRIMARY KEY (run_id, eid)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
"""


class SQLiteLogStore:
    def __init__(self, path: str | os.PathLike = ":memory:") -> None:
        self.path = str(path)
        # check_same_thread=False, and it is not optional. Strands runs a sync
        # tool through asyncio.to_thread, so a tool's memory writes reach this
        # connection from a worker thread. SQLite would raise ProgrammingError;
        # Strands converts that into a tool error; the agent reads a failed tool,
        # carries on, and finishes with a plausible answer - with every memory
        # write in the run gone and nothing announcing it.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        # A run performs one effect at a time, but sharing one connection across
        # threads is only safe if the threads never overlap inside it.
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def append(self, run_id: str, event: Event) -> None:
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO events (run_id, eid, type, seq, body) VALUES (?, ?, ?, ?, ?)",
                    (run_id, event.eid, event.type, getattr(event, "seq", None), encode_event(event)),
                )
            except sqlite3.IntegrityError:
                raise EventIdConflict(f"{run_id}: eid {event.eid} is already taken") from None
            self._db.commit()

    def read(self, run_id: str) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM events WHERE run_id = ? ORDER BY eid", (run_id,)
            ).fetchall()
        return [decode_event(body) for (body,) in rows]

    def put_metadata(self, metadata: RunMetadata) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (run_id, body) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET body = excluded.body",
                (metadata.run_id, encode_metadata(metadata)),
            )
            self._db.commit()

    def get_metadata(self, run_id: str) -> RunMetadata:
        with self._lock:
            row = self._db.execute("SELECT body FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return decode_metadata(row[0])

    def list_runs(self) -> list[RunMetadata]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM runs ORDER BY run_id").fetchall()
        return [decode_metadata(body) for (body,) in rows]
