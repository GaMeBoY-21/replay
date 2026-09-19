"""Serve the product on localhost.

    uv run python -m replay.local --db replay.local.db --seed fixtures/canonical
    uv run python -m replay.local --db replay.local.db --seed fixtures/canonical --replay-only
    uv run python -m replay.local --db replay.local.db --seed fixtures/canonical --corpus fixtures/corpus-qwen2.5-14b

The log is a SQLite file and the views are rebuilt in memory at startup. `--seed`
loads a directory of canonical runs into the log once, parents before forks.
Forking and resuming run the agent live, against the local Ollama model the
canonical runs were recorded with. `--replay-only` serves the recordings with no
model at all: /capabilities reports it, and the UI shows fork and resume as
unavailable - the safe mode for a demo. `--corpus` adds every recorded run in a
corpus directory, so each one can be opened, not only the canonical few.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from ..api import Runner
from ..kernel import BreakerConfig
from ..scenario.canonical import load
from ..scenario.corpus import load_into, load_run, run_files
from ..scenario.data import TASK
from ..scenario.live import OLLAMA_MODEL, build_agent
from ..store.sqlite import SQLiteLogStore
from ..store.views import MemoryViewStore
from .server import LocalApp, serve


def open_store(db, seed: pathlib.Path | None = None, corpus=()) -> SQLiteLogStore:
    """The local log, with the runs in `seed` and in each `corpus` directory
    added if it does not hold them yet.

    A corpus directory has no manifest: every run file in it is loaded as it is.
    A run id already in the log - the canonical runs are corpus runs too - is
    loaded once, from whichever directory came first.
    """
    store = SQLiteLogStore(db)
    if seed is not None:
        load(store, seed)
    for directory in corpus:
        add_corpus(store, directory)
    return store


def add_corpus(store, directory) -> list[str]:
    """Load every run file in a corpus directory the store does not hold yet."""
    known = {m.run_id for m in store.list_runs()}
    added = []
    for path in run_files(pathlib.Path(directory)):
        run_id = load_run(path)[0].run_id
        if run_id not in known:
            load_into(store, path)
            added.append(run_id)
    return added


def make_runner(replay_only: bool = False) -> Runner:
    """The runner the API drives runs with. Replay-only runs nothing live."""
    return Runner(factory=build_agent, prompt=TASK, breakers=BreakerConfig(max_effects=80),
                  live=not replay_only, model=None if replay_only else OLLAMA_MODEL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m replay.local")
    parser.add_argument("--db", default="replay.local.db")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=pathlib.Path, help="a directory of canonical runs, with its manifest.json")
    parser.add_argument("--static", type=pathlib.Path, help="the built frontend to serve under /")
    parser.add_argument("--corpus", type=pathlib.Path, action="append", default=[],
                        help="a directory of recorded runs to load as well; may be given more than once")
    parser.add_argument("--replay-only", action="store_true",
                        help="serve the recordings without a model: fork and resume are unavailable")
    args = parser.parse_args(argv)

    store = open_store(args.db, args.seed, args.corpus)

    app = LocalApp(store, MemoryViewStore(), make_runner(args.replay_only), static_dir=args.static)
    server = serve(app, args.host, args.port)
    mode = "replay only, no model" if args.replay_only else f"live on {OLLAMA_MODEL}"
    print(f"serving {len(store.list_runs())} runs from {args.db} on http://{args.host}:{args.port}"
          f"  (API under /api; {mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
