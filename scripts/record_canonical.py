"""Choose the canonical runs from the corpus, and record the fork from the wrong one.

    uv run python scripts/record_canonical.py --wrong qwen-00 --right qwen-01 \
        --why-wrong ... --why-right ... --halt-breaker depth --halt-ceiling N

The chosen runs are copied from fixtures/corpus-qwen2.5-14b unchanged. The fork is recorded
live, against the same local model, and committed whatever it does. Nothing is
re-run to get a better-looking result: one attempt, reported as it came out.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import tempfile

from replay.agent import runs
from replay.kernel import BreakerConfig, Breakers
from replay.scenario import TASK, substitution
from replay.scenario.corpus import export_run, load_into
from replay.kernel import resolve
from replay.scenario.live import OLLAMA_MODEL, build_agent, classify
from replay.store import SQLiteLogStore

REPO = pathlib.Path(__file__).resolve().parent.parent
CORPUS = REPO / "fixtures" / "corpus-qwen2.5-14b"
OUT = REPO / "fixtures" / "canonical"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrong", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--why-wrong", required=True)
    parser.add_argument("--why-right", required=True)
    parser.add_argument("--halt-breaker", choices=("loop", "depth"), required=True,
                        help="which breaker halts the run: whichever the corpus shows firing")
    parser.add_argument("--halt-ceiling", type=int, required=True,
                        help="that breaker's ceiling, set from what the corpus observed")
    parser.add_argument("--halt-attempts", type=int, default=5)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    store = SQLiteLogStore(pathlib.Path(tempfile.mkdtemp()) / "canonical.db")
    for run_id in (args.wrong, args.right):
        shutil.copyfile(CORPUS / f"{run_id}.json", OUT / f"{run_id}.json")
        load_into(store, OUT / f"{run_id}.json")

    seq, mutation = substitution(store, args.wrong)
    fork_id = f"{args.wrong}-fork-{seq}"
    try:
        runs.fork(store, args.wrong, seq, mutation, build_agent, TASK, run_id=fork_id,
                  breakers=Breakers(BreakerConfig(max_repeats=10**6, max_effects=80, max_tokens=10**9)))
    except Exception as exc:  # kept either way
        print(f"the fork raised {type(exc).__name__}: {exc}", flush=True)
    export_run(store, fork_id, OUT)

    # The halted run: live runs under the observed ceiling. Every attempt is kept
    # and counted; the first that trips is canonical. None is re-run.
    if args.halt_breaker == "loop":
        halt = BreakerConfig(max_repeats=args.halt_ceiling, max_effects=80, max_tokens=10**9)
    else:
        halt = BreakerConfig(max_repeats=10**6, max_effects=args.halt_ceiling, max_tokens=10**9)
    halted, attempts = None, []
    for n in range(args.halt_attempts):
        attempt = f"halt-{n:02d}"
        try:
            runs.record(store, build_agent, TASK, run_id=attempt, breakers=Breakers(halt))
        except Exception as exc:
            print(f"{attempt} raised {type(exc).__name__}: {exc}", flush=True)
        export_run(store, attempt, OUT)
        status = store.get_metadata(attempt).status.value
        attempts.append({"run_id": attempt, "status": status})
        print(f"{attempt}: {status}", flush=True)
        if status == "tripped":
            halted = attempt
            break

    manifest = {
        "model": OLLAMA_MODEL,
        "roots": [args.wrong, args.right] + [a["run_id"] for a in attempts],
        "wrong": {"run_id": args.wrong, "why": args.why_wrong},
        "right": {"run_id": args.right, "why": args.why_right},
        "fork": {"run_id": fork_id, "parent": args.wrong, "at_seq": seq,
                 "outcome": classify(resolve(store, fork_id).events, store.get_metadata(fork_id))["outcome"]},
        "halt": {"breaker": args.halt_breaker, "ceiling": args.halt_ceiling, "attempts": attempts},
    }
    if halted:
        manifest["halted"] = {"run_id": halted}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
