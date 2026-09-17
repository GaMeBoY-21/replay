"""The demo scenario, asserted against docs/SCENARIO.md.

The gate is the first test: flag Run B's output, walk back, land on step 3. The
rest hold the scenario to the properties the demo depends on - most of which are
invisible in the code and fatal on stage.
"""

from __future__ import annotations

import json

import pytest

from backends import new_store
from replay.kernel import diff_runs, flag_output, resolve, step_index, step_to_seq, trace_view
from replay.scenario import (
    FORK_A,
    RUN_A,
    RUN_B,
    record_canonical,
    resume_run_a,
)
from replay.scenario import data
from replay_events import MemoryRead, MemoryWrite, RunStatus


@pytest.fixture
def canonical():
    store = new_store()
    outcomes = record_canonical(store)
    return store, outcomes


def effect_names(events):
    steps = step_index(events)
    return {steps[e.eid]: getattr(e.effect, "name", "model") for e in events if e.type == "EffectRequested"}


# ---------------------------------------------------------------- the gate


def test_the_trace_from_run_bs_output_lands_on_the_currency_assumption_at_step_3(canonical):
    store, _ = canonical
    events = resolve(store, RUN_B).events
    view = trace_view(events, flag_output(events))

    assert view["output_step"] == 40
    assert (view["flagged"]["step"], view["flagged"]["key"]) == (39, "report.total")

    chain = [(link["step"], link["key"]) for link in view["chain"]]
    assert chain == [
        (3, "invoice.currency"),
        (4, "invoice.line_items"),
        (11, "invoice.subtotal"),
        (12, "fx.rate"),
        (13, "report.total"),
    ]
    head = view["head"]
    assert (head["step"], head["key"], head["value"]) == (3, "invoice.currency", "USD")


# ---------------------------------------------------------------- the constraint


def test_step_3_reads_no_prior_memory(canonical):
    """If the poisoning step read anything, the trace would correctly walk past
    it towards the run's origin, and the reveal would land on the wrong step."""
    store, _ = canonical
    events = resolve(store, RUN_B).events
    steps = step_index(events)

    at_step_3 = [e for e in events if steps[e.eid] == 3]
    assert [e for e in at_step_3 if isinstance(e, MemoryRead)] == []
    (write,) = [e for e in at_step_3 if isinstance(e, MemoryWrite)]
    assert (write.key, write.value, write.reads) == ("invoice.currency", "USD", [])


def test_memory_is_touched_where_the_scenario_table_says(canonical):
    store, _ = canonical
    events = resolve(store, RUN_B).events
    steps = step_index(events)
    touched: dict[str, dict[str, list[int]]] = {}
    for event in events:
        if isinstance(event, (MemoryRead, MemoryWrite)):
            kind = "written" if isinstance(event, MemoryWrite) else "read"
            touched.setdefault(event.key, {"written": [], "read": []})[kind].append(steps[event.eid])

    assert touched == {
        "invoice.currency": {"written": [3], "read": [4, 7, 9, 12, 33]},
        # 6, 7 and 9 are the tool steps inside 5-10; 11 sums the items, which
        # the step table has reading them and the memory-key table omits.
        "invoice.line_items": {"written": [4], "read": [6, 7, 9, 11]},
        "invoice.subtotal": {"written": [11], "read": [12, 13]},
        "fx.rate": {"written": [12], "read": [13]},
        "report.total": {"written": [13], "read": [33, 39]},
    }


def test_step_33_checks_its_own_work_and_agrees(canonical):
    store, _ = canonical
    events = resolve(store, RUN_B).events
    steps = step_index(events)
    (check,) = [e for e in events if e.type == "EffectCompleted" and steps[e.eid] == 33]
    assert json.loads(check.result.value["content"][0]["text"])["plausible"] is True


# ---------------------------------------------------------------- the data


def test_the_invoice_header_has_no_currency_field(canonical):
    assert "currency" not in data.HEADERS["INV-2291"]
    store, _ = canonical
    events = resolve(store, RUN_B).events
    steps = step_index(events)
    (header,) = [e for e in events if e.type == "EffectCompleted" and steps[e.eid] == 3]
    assert "currency" not in json.loads(header.result.value["content"][0]["text"])


def test_the_line_items_sum_to_41000():
    items = data.LINE_ITEMS["INV-2291"]
    assert len(items) == 6
    assert sum(item["amount"] for item in items) == 41000


# ---------------------------------------------------------------- steps and seqs


def test_step_to_seq_maps_the_scenario_steps(canonical):
    store, _ = canonical
    events = resolve(store, RUN_B).events
    mapping = step_to_seq(events)
    names = effect_names(events)

    assert len(mapping) == 40 and sorted(mapping) == list(range(1, 41))
    assert names[3] == "get_invoice_header" and mapping[3] == 2
    assert names[12] == "fx_convert" and mapping[12] == 11
    assert names[40] == "model"


def test_forking_step_12_replaces_fx_convert(canonical):
    store, _ = canonical
    metadata = store.get_metadata(FORK_A)
    assert metadata.forked_at_seq == step_to_seq(resolve(store, RUN_B).events)[12]

    (substituted,) = [e for e in store.read(FORK_A) if e.type == "EffectCompleted" and e.substituted]
    requested = resolve(store, FORK_A).at(substituted.seq).effect
    assert requested.name == "fx_convert"
    assert "source currency INR" in substituted.result.value["content"][0]["text"]


# ---------------------------------------------------------------- the runs


def test_run_b_is_wrong_and_the_fork_is_right(canonical):
    store, outcomes = canonical
    assert outcomes[RUN_B].answer.strip() == "Total: $41,000.00"
    assert outcomes[FORK_A].answer.strip() == "Total: $492.00"

    fork_state = outcomes[FORK_A].agent.state._inner.get()
    assert fork_state["invoice.currency"] == "INR", "the agent read the warning and corrected its record"
    assert fork_state["report.total"] == 492.0


def test_the_fork_shares_eleven_steps_by_storage(canonical):
    store, _ = canonical
    diff = diff_runs(store, RUN_B, FORK_A)
    assert (diff["shared_prefix"], diff["shared_by"], diff["divergence_seq"]) == (11, "storage", 11)
    assert all(row["shared"] for row in diff["rows"][:11]) and not diff["rows"][11]["shared"]

    own = store.read(FORK_A)
    resolved = resolve(store, FORK_A).events
    assert own[0].seq == 11
    assert len(own) == len([e for e in resolved if e.eid >= own[0].eid]) < len(resolved)


def test_run_a_halts_on_the_fifth_vendor_lookup(canonical):
    store, outcomes = canonical
    assert outcomes[RUN_A].status == RunStatus.TRIPPED
    events = resolve(store, RUN_A).events
    steps = step_index(events)

    completed_lookups = [
        e for e in events if e.type == "EffectRequested" and getattr(e.effect, "name", None) == "vendor_lookup"
    ]
    assert [steps[e.eid] for e in completed_lookups] == [16, 18, 20, 22], "four attempts execute"
    assert len({e.effect.shape() for e in completed_lookups}) == 1, "the retries are byte-identical"

    (trip,) = [e for e in events if e.type == "BreakerTripped"]
    assert steps[trip.eid] == 24 and trip.breaker == "loop"
    assert step_to_seq(events)[24] == trip.seq
    assert effect_names(events) == {k: v for k, v in effect_names(resolve(store, RUN_B).events).items() if k < 24}


def test_the_breaker_message_names_no_step(canonical):
    store, _ = canonical
    (trip,) = [e for e in resolve(store, RUN_A).events if e.type == "BreakerTripped"]
    assert trip.detail == "Same call attempted 5 times. Suspended."
    assert "step" not in trip.detail.lower() and str(trip.seq) not in trip.detail


def test_run_a_resumes_with_an_override_and_re_trips_without_one(canonical):
    store, _ = canonical
    halted_seq = next(e.seq for e in store.read(RUN_A) if e.type == "BreakerTripped")

    again = resume_run_a(store, run_id="run-a-again")
    assert again.status == RunStatus.TRIPPED
    assert store.read("run-a-again")[0].type == "BreakerTripped"
    assert store.read("run-a-again")[0].seq == halted_seq

    resumed = resume_run_a(store, run_id="run-a-resumed", breaker_overrides={"max_repeats": 10})
    assert resumed.status == RunStatus.COMPLETED
    assert resumed.answer.strip() == "Total: $41,000.00"
