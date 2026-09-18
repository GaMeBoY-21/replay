"""The corpora: the failure rate is a number anyone can recompute.

Every run of the task recorded from a real model is committed, one directory per
model. These tests recompute the statistics from those files, check that every
run in a corpus was given the same thing, and replay every completed run with a
model that refuses to be called - which also proves the tools' specs have not
changed since they were recorded.

    fixtures/corpus-qwen2.5-14b/   the active corpus; the canonical runs come from it
    fixtures/corpus-gemma4-12b/    archived evidence that gemma4:12b cannot operate
                                   the tools. Kept as recorded, never repaired.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import strands_harness as H
from replay.agent import runs
from replay.scenario.corpus import load_into, load_run, run_files, summarise
from replay.scenario.data import TASK
from replay.scenario.live import build_agent
from replay.store import MemoryLogStore
from replay_events import EffectRequested

pytestmark = pytest.mark.single_backend

REPO = pathlib.Path(__file__).resolve().parent.parent
ACTIVE = REPO / "fixtures" / "corpus-qwen2.5-14b"
ARCHIVE = REPO / "fixtures" / "corpus-gemma4-12b"
CORPORA = {"qwen2.5-14b": ACTIVE, "gemma4-12b": ARCHIVE}

# Three runs to decide whether the model could operate the tools at all, then
# eight more once it could. The archive is exempt: it stopped at fifteen runs,
# and what it establishes does not depend on its size.
ACTIVE_RUNS = 11

# The runs whose replayed answer is known to differ from the recorded one, and why.
# Gemma ended corpus-09 with raw chat-template text instead of an answer; the
# summary reads that text as the answer and the replayed agent reads it as empty.
# See fixtures/corpus-gemma4-12b/NOTES.md. Pinned, not skipped: if it ever
# replays equal, this entry is wrong.
TEMPLATE_LEAKS = {"corpus-09": "thought\n<channel|>"}


def all_runs():
    return [pytest.param(path, id=f"{name}/{path.stem}")
            for name, directory in CORPORA.items() for path in run_files(directory)]


def test_the_active_corpus_has_every_run_it_was_meant_to():
    assert len(run_files(ACTIVE)) >= ACTIVE_RUNS


@pytest.mark.parametrize(("directory", "model", "context"), [
    (ACTIVE, "qwen2.5:14b", 16384),
    (ARCHIVE, "gemma4:12b", 32768),
], ids=list(CORPORA))
def test_every_run_in_a_corpus_was_recorded_under_one_provider_configuration(directory, model, context):
    provider = json.loads((directory / "provider.json").read_text())
    assert (provider["model"], provider["context_length"]) == (model, context)


@pytest.mark.parametrize("directory", CORPORA.values(), ids=list(CORPORA))
def test_the_statistics_are_reproducible_from_the_committed_runs(directory):
    committed = json.loads((directory / "stats.json").read_text())
    assert summarise(run_files(directory)) == committed


@pytest.mark.parametrize("directory", CORPORA.values(), ids=list(CORPORA))
def test_every_run_was_given_exactly_the_same_input(directory):
    """Same task, same system prompt, same tools, every time. Nothing was tuned
    between runs to push the model one way."""
    first_requests = set()
    for path in run_files(directory):
        _, events = load_run(path)
        first = next(e for e in events if isinstance(e, EffectRequested))
        assert first.effect.effect_kind == "model"
        first_requests.add(first.effect.shape())
    assert len(first_requests) == 1
    (shape,) = first_requests
    assert TASK in shape


@pytest.mark.parametrize("path", all_runs())
def test_every_completed_run_replays_without_the_model(path):
    metadata, _ = load_run(path)
    if metadata.status.value != "completed":
        pytest.skip(f"{path.stem} did not complete ({metadata.status.value}); there is no whole run to replay")
    store = MemoryLogStore()
    load_into(store, path)
    H.reset_witnesses()
    replayed = runs.replay(store, metadata.run_id, lambda: build_agent(model=H.RefusingModel()), TASK)
    recorded = {row["run_id"]: row for row in summarise([path])["rows"]}[metadata.run_id]["answer"] or ""
    if path.parent == ARCHIVE and metadata.run_id in TEMPLATE_LEAKS:
        assert (recorded, replayed.answer.strip()) == (TEMPLATE_LEAKS[metadata.run_id], "")
    else:
        assert replayed.answer.strip() == recorded
