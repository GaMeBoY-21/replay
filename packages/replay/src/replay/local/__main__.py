"""Serve the product on localhost.

    uv run python -m replay.local --db replay.local.db

The log is a SQLite file and the views are rebuilt in memory at startup, from
whatever runs the log already holds. Forking and resuming run the scenario's
agent live. Seeding the log with the canonical runs is not wired here yet: those
runs are recorded from a real model and are not committed.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from ..api import Runner
from ..kernel import BreakerConfig
from ..scenario import TASK, build_agent
from ..store.sqlite import SQLiteLogStore
from ..store.views import MemoryViewStore
from .server import LocalApp, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.local")
    parser.add_argument("--db", default="replay.local.db")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--static", type=pathlib.Path, help="the built frontend to serve under /")
    args = parser.parse_args(argv)

    store = SQLiteLogStore(args.db)
    runner = Runner(factory=build_agent, prompt=TASK, breakers=BreakerConfig(max_effects=80))
    app = LocalApp(store, MemoryViewStore(), runner, static_dir=args.static)
    server = serve(app, args.host, args.port)
    print(f"serving {len(store.list_runs())} runs from {args.db} on http://{args.host}:{args.port}  (API under /api)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
