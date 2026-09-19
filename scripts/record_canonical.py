"""Choose the canonical runs from the corpus, and record the fork from the wrong one.

    uv run python scripts/record_canonical.py --fork-only

re-records only the fork, from the committed wrong and right runs, and leaves
every other canonical run as it is.

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
from replay.scenario import TASK, decision_seq, substitution
from replay.scenario.corpus import export_run, load_into
from replay.kernel import resolve
from replay.scenario.live import OLLAMA_MODEL, build_agent, classify
from replay.store import SQLiteLogStore

REPO = pathlib.Path(__file__).resolve().parent.parent
CORPUS = REPO / "fixtures" / "corpus-qwen2.5-14b"
OUT = REPO / "fixtures" / "canonical"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fork-only", action="store_true",
                        help="re-record the fork alone, from the committed canonical runs")
    parser.add_argument("--wrong")
    parser.add_argument("--right")
    parser.add_argument("--why-wrong")
    parser.add_argument("--why-right")
    parser.add_argument("--halt-breaker", choices=("loop", "depth"),
                        help="which breaker halts the run")
    parser.add_argument("--halt-ceiling", type=int, help="that breaker's ceiling")
    parser.add_argument("--halt-attempts", type=int, default=5)
    args = parser.parse_args()
    if args.fork_only:
        return fork_only()

    OUT.mkdir(parents=True, exist_ok=True)
    store = SQLiteLogStore(pathlib.Path(tempfile.mkdtemp()) / "canonical.db")
    for run_id in (args.wrong, args.right):
        shutil.copyfile(CORPUS / f"{run_id}.json", OUT / f"{run_id}.json")
        load_into(store, OUT / f"{run_id}.json")

    fork = record_fork(store, args.wrong, args.right)

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
        "fork": fork,
        "halt": {"breaker": args.halt_breaker, "ceiling": args.halt_ceiling, "attempts": attempts},
    }
    if halted:
        manifest["halted"] = {"run_id": halted}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


def record_fork(store, wrong: str, right: str) -> dict:
    """Fork the wrong run at its decision, serving the right run's recorded
    response there, and record whatever happens next. One attempt."""
    seq, mutation = substitution(store, wrong, right)
    fork_id = f"{wrong}-fork-{seq}"
    try:
        runs.fork(store, wrong, seq, mutation, build_agent, TASK, run_id=fork_id,
                  breakers=Breakers(BreakerConfig(max_repeats=10**6, max_effects=80, max_tokens=10**9)))
    except Exception as exc:  # kept either way
        print(f"the fork raised {type(exc).__name__}: {exc}", flush=True)
    export_run(store, fork_id, OUT)
    return {"run_id": fork_id, "parent": wrong, "at_seq": seq,
            "substituted_from": {"run_id": right, "seq": decision_seq(resolve(store, right).events)},
            "outcome": classify(resolve(store, fork_id).events, store.get_metadata(fork_id))["outcome"]}


def fork_only() -> int:
    manifest = json.loads((OUT / "manifest.json").read_text())
    store = SQLiteLogStore(pathlib.Path(tempfile.mkdtemp()) / "canonical.db")
    for run_id in manifest["roots"]:
        load_into(store, OUT / f"{run_id}.json")
    previous = OUT / f"{manifest['fork']['run_id']}.json"
    manifest["fork"] = record_fork(store, manifest["wrong"]["run_id"], manifest["right"]["run_id"])
    if previous.name != f"{manifest['fork']['run_id']}.json":
        previous.unlink()
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["fork"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
