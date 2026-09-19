"""Load the recorded runs into the deployed stack.

    uv run python scripts/seed_aws.py

Table and bucket names come from ReplayStack's outputs. Loads fixtures/canonical
in its manifest's order - parents before forks - and then every run in the
qwen2.5:14b corpus, through the same `canonical.load` and `add_corpus` the local
server uses. A run id already in the log is skipped, so it is safe to run twice.
Then every run's projection is rebuilt directly - rather than waiting on the
Streams projector - and the views table is checked for a summary of each.
"""

from __future__ import annotations

import pathlib
import sys

import boto3

from replay.local.__main__ import add_corpus
from replay.projector.projector import rebuild_all
from replay.scenario import load
from replay.store.dynamo import DynamoLogStore
from replay.store.views import DynamoViewStore

REPO = pathlib.Path(__file__).resolve().parent.parent
REGION = "ap-south-1"
STACK = "ReplayStack"


def outputs() -> dict[str, str]:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def main() -> int:
    out = outputs()
    store = DynamoLogStore(out["EventsTable"], out["PayloadBucket"], region_name=REGION)
    views = DynamoViewStore(out["ViewsTable"], region_name=REGION)

    before = {m.run_id for m in store.list_runs()}
    load(store, REPO / "fixtures" / "canonical")
    add_corpus(store, REPO / "fixtures" / "corpus-qwen2.5-14b")
    runs = sorted(m.run_id for m in store.list_runs())
    added = sorted(set(runs) - before)

    rebuild_all(store, views)
    summarised = {row["run_id"] for row in views.list_summaries()}
    missing = sorted(set(runs) - summarised)

    print(f"runs in the log: {len(runs)}  (added now: {len(added)}, already there: {len(before)})")
    print(f"summaries in the views table: {len(summarised & set(runs))} of {len(runs)}")
    if missing:
        print(f"no summary for: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
