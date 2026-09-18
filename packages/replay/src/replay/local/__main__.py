"""Serve the product on localhost.

    uv run python -m replay.local --db replay.local.db --seed fixtures/canonical

The log is a SQLite file and the views are rebuilt in memory at startup. `--seed`
loads a directory of canonical runs into the log once, parents before forks. Forking and resuming run the agent live,
against the local Ollama model the canonical runs were recorded with.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from ..api import Runner
from ..kernel import BreakerConfig
from ..scenario.canonical import load
from ..scenario.data import TASK
from ..scenario.live import build_agent
from ..store.sqlite import SQLiteLogStore
from ..store.views import MemoryViewStore
from .server import LocalApp, serve


def open_store(db, seed: pathlib.Path | None = None) -> SQLiteLogStore:
    """The local log, with the runs in `seed` added if it does not hold them yet."""
    store = SQLiteLogStore(db)
    if seed is not None:
        load(store, seed)
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.local")
    parser.add_argument("--db", default="replay.local.db")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=pathlib.Path, help="a directory of canonical runs, with its manifest.json")
    parser.add_argument("--static", type=pathlib.Path, help="the built frontend to serve under /")
    args = parser.parse_args(argv)

    store = open_store(args.db, args.seed)

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
