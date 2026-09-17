"""Checks that compare the backends, or look inside one. Each runs once.

The shared suite proves the backends answer the protocol alike. These prove the
two things it cannot see from outside: that a real agent's writes survive
SQLite's threading, and that DynamoDB stores what §9 says it stores.
"""

from __future__ import annotations

import json

import pytest

import strands_harness as H
from backends import BACKENDS, PAYLOAD_BUCKET, open_backend
from replay.agent import runs
from replay.kernel import resolve
from replay.store import SQLiteLogStore
from replay_events import MemoryWrite, Result, dump_event

pytestmark = pytest.mark.single_backend

MUTATION = Result(value={"toolUseId": "tooluse-2", "status": "success", "content": [{"text": "x"}]})


def live():
    return H.build_agent()


def test_a_real_agent_run_keeps_its_memory_writes_on_sqlite(tmp_path):
    """Strands runs a sync tool on a worker thread. Without check_same_thread=False
    SQLite raises there, Strands turns it into a tool error, and the run still
    finishes - with every memory write gone. So the writes are what is asserted."""
    store = SQLiteLogStore(tmp_path / "agent.db")
    outcome = runs.record(store, live, H.PROMPT, run_id="sqlite-run")

    assert outcome.answer.strip() == "Total: $41,000.00"
    writes = {e.key: e.value for e in store.read("sqlite-run") if isinstance(e, MemoryWrite)}
    assert writes == {"invoice.currency": "USD", "fx.rate": 1.0, "report.total": 41000}

    reopened = SQLiteLogStore(tmp_path / "agent.db")
    assert len(reopened.read("sqlite-run")) == len(store.read("sqlite-run")), "the writes did not persist"


def comparable(events):
    """Everything except the append timestamp, which is a clock read per run."""
    out = []
    for event in events:
        dumped = dump_event(event)
        dumped.pop("ts")
        out.append(dumped)
    return json.dumps(out, sort_keys=True)


def test_the_same_run_resolves_identically_on_every_backend(tmp_path):
    resolved = {}
    for name in BACKENDS:
        with open_backend(name, tmp_path / name) as factory:
            (tmp_path / name).mkdir(exist_ok=True)
            store = factory()
            runs.record(store, live, H.PROMPT, run_id="root")
            runs.fork(store, "root", 2, MUTATION, live, H.PROMPT, run_id="fork")
            resolved[name] = comparable(resolve(store, "fork").events)

    assert resolved["sqlite"] == resolved["memory"]
    assert resolved["dynamodb"] == resolved["memory"]


def test_the_suite_runs_every_store_test_against_every_backend():
    """The equivalence claim rests on the parametrisation actually happening.
    Without it every test still passes - on memory alone - and nothing says so."""
    import pathlib
    import subprocess
    import sys

    repo = pathlib.Path(__file__).resolve().parent.parent
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
         "tests/test_stores.py", "tests/test_gate_fork.py"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.splitlines()
    ids = [line for line in collected if "::" in line]
    assert ids, "nothing was collected"
    for backend in BACKENDS:
        tagged = [i for i in ids if i.endswith(f"[{backend}]")]
        assert len(tagged) * len(BACKENDS) == len(ids), f"not every test runs on {backend}"


def test_dynamodb_items_follow_the_documented_data_model(tmp_path):
    from replay_events import EffectCompleted, StepBoundary

    with open_backend("dynamodb", tmp_path) as factory:
        store = factory()
        store.append("run", StepBoundary(eid=12))
        store.append("run", EffectCompleted(eid=13, seq=0, result=Result(value="y" * 150_000)))

        items = store._client.query(
            TableName=store.table_name,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": "RUN#run"}},
        )["Items"]
        by_sk = {item["SK"]["S"]: item for item in items}

        assert set(by_sk) == {"EVT#000000012", "EVT#000000013"}
        small, large = by_sk["EVT#000000012"], by_sk["EVT#000000013"]
        assert "payload" in small and "payload_ref" not in small
        assert "payload_ref" in large and "payload" not in large, "payload and payload_ref are exclusive"
        assert large["payload_ref"]["S"].startswith(f"s3://{PAYLOAD_BUCKET}/")
