"""Record the task against a real model, one run at a time, into a corpus directory.

    uv run python scripts/record_corpus.py --out fixtures/corpus-qwen2.5-14b --runs 3

Needs a running Ollama with the model pulled (REPLAY_OLLAMA_MODEL, default
qwen2.5:14b). One directory per model: its provider.json refuses a second
configuration. Every run is kept, whatever it did. Runs already exported are
skipped, so an interrupted recording resumes. Nothing about the task, the tools
or the provider's sampling changes between runs.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time

from replay.agent import runs
from replay.kernel import BreakerConfig, Breakers
from replay.scenario.corpus import export_run, run_files, summarise
from replay.scenario.data import TASK
from replay.scenario.live import OLLAMA_MODEL, build_agent
from replay.store import SQLiteLogStore

# Not armed against loops: the corpus observes how the model retries. The depth
# ceiling only stops a runaway from recording forever.
CORPUS_BREAKERS = BreakerConfig(max_repeats=10**6, max_effects=80, max_tokens=10**9)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--prefix", default="corpus")
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    OUT = args.out

    OUT.mkdir(parents=True, exist_ok=True)
    # The provider configuration every run in this directory was recorded under.
    # One configuration for the whole corpus, or the rate means nothing.
    provider = {"model": OLLAMA_MODEL, "server": "ollama", "context_length": int(os.environ["OLLAMA_CONTEXT_LENGTH"]),
                "sampling": "the model's defaults; no parameters are sent"}
    path = OUT / "provider.json"
    if path.exists() and json.loads(path.read_text()) != provider:
        sys.exit(f"{path} records a different provider configuration; this would mix two corpora")
    path.write_text(json.dumps(provider, indent=2, sort_keys=True) + "\n")
    store = SQLiteLogStore(pathlib.Path(tempfile.mkdtemp()) / "corpus.db")
    for n in range(args.start, args.start + args.runs):
        run_id = f"{args.prefix}-{n:02d}"
        if (OUT / f"{run_id}.json").exists():
            print(f"{run_id} already recorded", flush=True)
            continue
        started = time.monotonic()
        try:
            runs.record(store, build_agent, TASK, run_id=run_id, breakers=Breakers(CORPUS_BREAKERS))
        except Exception as exc:  # a failed run is still a run
            print(f"{run_id} raised {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        export_run(store, run_id, OUT)
        print(f"{run_id} recorded in {time.monotonic() - started:.0f}s with {OLLAMA_MODEL}", flush=True)

    stats = summarise(run_files(OUT))
    print(json.dumps({k: v for k, v in stats.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
