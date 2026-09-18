"""The scenario, asserted against runs a real model produced.

Nothing here asserts a step number. A real model does not put its assumption at
step 3; what is asserted is the structure the demo depends on - that the run
went wrong, that the trace lands on the write that recorded the assumption, and
that the model could have got it right.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import strands_harness as H
from backends import new_store
from replay.agent import runs
from replay.kernel import diff_runs, flag_output, resolve, step_index, trace_view
from replay.scenario import SUBSTITUTED, TASK, conversion_seq, load
from replay.scenario.corpus import load_run, run_files, summarise
from replay.scenario.live import build_agent, classify
from replay_events import BreakerTripped, EffectRequested, MemoryWrite

CANONICAL = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "canonical"


@pytest.fixture
def canonical():
    store = new_store()
    manifest = load(store, CANONICAL)
    return store, manifest


def outcome(store, run_id):
    return classify(resolve(store, run_id).events, store.get_metadata(run_id))


# ---------------------------------------------------------------- the gate


def test_the_trace_of_the_wrong_run_lands_on_the_write_that_recorded_the_assumption(canonical):
    store, manifest = canonical
    wrong = manifest["wrong"]["run_id"]
    assert outcome(store, wrong)["outcome"] == "wrong"

    events = resolve(store, wrong).events
    view = trace_view(events, flag_output(events))
    head = view["head"]
    assert (head["key"], head["value"]) == ("invoice.currency", "USD")
    assert "report.total" in [link["key"] for link in view["chain"]]

    by_eid = {e.eid: e for e in events}
    assert by_eid[head["eid"]].reads == [], "the assumption read no prior memory"


def test_the_assumption_was_recorded_by_the_model_through_the_recording_tool(canonical):
    store, manifest = canonical
    events = resolve(store, manifest["wrong"]["run_id"]).events
    steps = step_index(events)
    (write,) = [e for e in events if isinstance(e, MemoryWrite) and e.key == "invoice.currency"]
    (tool,) = [e for e in events if isinstance(e, EffectRequested) and steps[e.eid] == steps[write.eid]]
    assert tool.effect.name == "record_invoice_field"
    assert tool.effect.arguments["value"].strip().upper() == "USD"


# ---------------------------------------------------------------- the contrast


def test_the_model_could_have_got_it_right(canonical):
    store, manifest = canonical
    right = outcome(store, manifest["right"]["run_id"])
    assert right["outcome"] == "right"
    assert right["currency_used"].upper() == "INR"
    assert right["converted_total"] == 492.0


def test_the_right_and_wrong_runs_were_asked_the_same_thing(canonical):
    """They differ in the assumption and what follows from it, not in the input."""
    store, manifest = canonical
    first = {}
    for role in ("wrong", "right"):
        events = resolve(store, manifest[role]["run_id"]).events
        first[role] = next(e for e in events if isinstance(e, EffectRequested)).effect.shape()
    assert first["wrong"] == first["right"]

    wrong, right = outcome(store, manifest["wrong"]["run_id"]), outcome(store, manifest["right"]["run_id"])
    assert (wrong["currency_used"].upper(), right["currency_used"].upper()) == ("USD", "INR")
    assert (wrong["converted_total"], right["converted_total"]) == (41000.0, 492.0)


# ---------------------------------------------------------------- the fork


def test_the_fork_replaces_the_conversion_and_owns_its_log_from_there(canonical):
    store, manifest = canonical
    fork, parent = manifest["fork"]["run_id"], manifest["wrong"]["run_id"]
    metadata = store.get_metadata(fork)
    assert metadata.parent_run_id == parent
    assert metadata.forked_at_seq == conversion_seq(resolve(store, parent).events) == manifest["fork"]["at_seq"]

    own = store.read(fork)
    assert own[0].type == "EffectRequested" and own[0].seq == metadata.forked_at_seq
    assert own[0].effect.name == "convert_invoice_total"
    assert own[1].substituted and json.loads(own[1].result.value["content"][0]["text"]) == SUBSTITUTED


def test_the_diff_shares_the_prefix_up_to_the_conversion(canonical):
    store, manifest = canonical
    diff = diff_runs(store, manifest["wrong"]["run_id"], manifest["fork"]["run_id"])
    assert (diff["shared_by"], diff["shared_prefix"]) == ("storage", manifest["fork"]["at_seq"])


def test_the_fork_replays_as_recorded_without_the_model(canonical):
    store, manifest = canonical
    fork = manifest["fork"]["run_id"]
    metadata = store.get_metadata(fork)
    if metadata.status.value != "completed":
        pytest.skip(f"the fork ended {metadata.status.value}; it is kept as recorded")
    H.reset_witnesses()
    replayed = runs.replay(store, fork, lambda: build_agent(model=H.RefusingModel()), TASK)
    assert replayed.answer.strip() == (outcome(store, fork)["answer"] or "")


def test_the_manifest_reports_what_the_fork_actually_did(canonical):
    store, manifest = canonical
    assert manifest["fork"]["outcome"] == outcome(store, manifest["fork"]["run_id"])["outcome"]


# ---------------------------------------------------------------- the halt


def test_the_halted_run_was_stopped_by_the_breaker_the_manifest_names(canonical):
    store, manifest = canonical
    halted = manifest["halted"]["run_id"]
    metadata = store.get_metadata(halted)
    assert metadata.status.value == "tripped"
    ceiling_key = {"loop": "max_repeats", "depth": "max_effects"}[manifest["halt"]["breaker"]]
    assert metadata.breaker_config[ceiling_key] == manifest["halt"]["ceiling"]
    (trip,) = [e for e in store.read(halted) if isinstance(e, BreakerTripped)]
    assert trip.breaker == manifest["halt"]["breaker"]


def test_every_halt_attempt_is_kept_and_only_the_last_tripped():
    """Attempts are not re-rolled: each is committed, and recording stopped at the
    first one the breaker halted."""
    manifest = json.loads((CANONICAL / "manifest.json").read_text())
    attempts = manifest["halt"]["attempts"]
    assert [(CANONICAL / f"{a['run_id']}.json").exists() for a in attempts] == [True] * len(attempts)
    assert [a["status"] for a in attempts] == ["completed"] * (len(attempts) - 1) + ["tripped"]
    assert manifest["halted"]["run_id"] == attempts[-1]["run_id"]


def test_the_depth_ceiling_is_one_the_corpus_itself_exceeds():
    """A ceiling no recorded run reaches would be decoration; one every run
    reaches would halt the task itself. The corpus has runs on both sides."""
    manifest = json.loads((CANONICAL / "manifest.json").read_text())
    if manifest["halt"]["breaker"] != "depth":
        pytest.skip("the halt is a loop halt")
    corpus = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "corpus-qwen2.5-14b"
    effects = [sum(isinstance(e, EffectRequested) for e in load_run(p)[1]) for p in run_files(corpus)]
    ceiling = manifest["halt"]["ceiling"]
    assert any(n > ceiling for n in effects) and any(n <= ceiling for n in effects)


def test_the_canonical_runs_are_the_corpus_runs_unchanged():
    corpus = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "corpus-qwen2.5-14b"
    manifest = json.loads((CANONICAL / "manifest.json").read_text())
    for role in ("wrong", "right"):
        run_id = manifest[role]["run_id"]
        assert (CANONICAL / f"{run_id}.json").read_text() == (corpus / f"{run_id}.json").read_text()
        stats = {row["run_id"]: row for row in summarise([corpus / f"{run_id}.json"])["rows"]}
        assert stats[run_id]["overall"] == role
