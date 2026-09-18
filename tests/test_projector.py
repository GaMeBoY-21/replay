"""The projector: idempotent by reconstruction, against every store."""

from __future__ import annotations

import json
import random

import strands_harness as H
from backends import new_store, new_views
from replay.agent import runs
from replay.api.live import fan_out, subscribe
from replay.kernel import BreakerConfig, Breakers
from replay.projector import projector
from replay.projector.views import answer, summary
from replay.kernel import resolve
from replay_events import Result

MUTATION = Result(value={"toolUseId": "tooluse-2", "status": "success", "content": [{"text": "x"}]})


def stream_records(store, run_id):
    """What DynamoDB Streams would deliver for a run: one INSERT per item."""
    records = [{"eventName": "INSERT",
                "dynamodb": {"Keys": {"PK": {"S": f"RUN#{run_id}"}, "SK": {"S": f"EVT#{e.eid:09d}"}}}}
               for e in store.read(run_id)]
    records.append({"eventName": "MODIFY",
                    "dynamodb": {"Keys": {"PK": {"S": f"RUN#{run_id}"}, "SK": {"S": "METADATA"}}}})
    return records


def recorded():
    store = new_store()
    runs.record(store, H.build_agent, H.PROMPT, run_id="root")
    runs.fork(store, "root", 2, MUTATION, H.build_agent, H.PROMPT, run_id="fork")
    return store


def test_a_batch_is_reduced_to_the_runs_it_touched():
    store = recorded()
    records = stream_records(store, "root") + stream_records(store, "fork") + stream_records(store, "root")
    assert projector.affected_runs({"Records": records}) == ["root", "fork"]


def test_the_same_batch_twice_shuffled_projects_the_same_rows():
    store = recorded()
    records = stream_records(store, "root") + stream_records(store, "fork")

    once = new_views()
    projector.project({"Records": records}, store, once)

    # At-least-once: the same records delivered again, as a separate batch, in a
    # different order. A projector that updated rows incrementally would count
    # the second delivery on top of the first.
    twice = new_views()
    shuffled = list(records)
    random.Random(7).shuffle(shuffled)
    projector.project({"Records": shuffled}, store, twice)
    random.Random(11).shuffle(shuffled)
    projector.project({"Records": shuffled}, store, twice)

    assert json.dumps(twice.list_summaries(), sort_keys=True) == json.dumps(once.list_summaries(), sort_keys=True)
    assert twice.tree("root") == once.tree("root")


def test_a_metadata_change_alone_reprojects_its_run():
    store = recorded()
    views = new_views()
    metadata_only = [r for r in stream_records(store, "root") if r["dynamodb"]["Keys"]["SK"]["S"] == "METADATA"]
    projector.project({"Records": metadata_only}, store, views)
    assert views.get_summary("root")["status"] == "completed"


def test_the_answer_is_recovered_from_the_log():
    store = recorded()
    events = resolve(store, "root").events
    assert answer(events) == "Total: $41,000.00"
    views = new_views()
    projector.project_run(store, views, "root")
    assert views.get_summary("root")["answer"] == "Total: $41,000.00"


def test_a_halted_run_names_its_halted_step():
    store = new_store()
    looping = [H.tool_response(H.tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, f"l{n}"))
               for n in range(5)]
    runs.record(store, lambda: H.build_agent(model=H.ScriptedModel(looping)), H.PROMPT, run_id="halted",
                breakers=Breakers(BreakerConfig(max_repeats=3)))
    row = summary(resolve(store, "halted").events, store.get_metadata("halted"))
    assert row["status"] == "tripped"
    assert row["halted"]["name"] == "loop"
    assert row["halted"]["detail"] == "Same call attempted 3 times. Suspended."
    assert row["halted"]["step"] == row["step_count"], "the last row IS the halt, not a blank"


def test_forks_are_grouped_under_their_root():
    store = recorded()
    views = new_views()
    projector.rebuild_all(store, views)
    tree = views.tree("root")
    assert [(e["run_id"], e["parent_run_id"]) for e in tree] == [("fork", "root"), ("root", None)]


def test_the_live_feed_is_written_and_tested_though_not_deployed():
    connections: dict[str, set[str]] = {}
    assert subscribe(connections, "c1", json.dumps({"run_id": "root"})) == {"subscribed": "root"}
    assert subscribe(connections, "c2", json.dumps({"run_id": "root"})) == {"subscribed": "root"}
    assert "error" in subscribe(connections, "c3", "not json")

    sent = []
    result = fan_out({"root": {"status": "completed"}}, connections,
                     lambda cid, msg: sent.append(cid) or cid == "c1")
    assert result == {"delivered": 1} and sent == ["c1", "c2"]
    assert connections["root"] == {"c1"}, "the dead connection was dropped"


def test_projections_carry_no_events_only_derived_rows():
    store = recorded()
    views = new_views()
    projector.project_run(store, views, "root")
    row = views.get_summary("root")
    assert set(row) == {"run_id", "status", "parent_run_id", "forked_at_seq", "answer", "step_count",
                        "effect_count", "halted"}
    assert json.dumps(row).count("eid") == 0
