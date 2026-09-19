"""The canonical runs: chosen from the corpus, not authored.

    fixtures/canonical/
      manifest.json      which run plays which role, and why it was chosen
      <run_id>.json      each run, as recorded - metadata and events

The wrong run and the right run are corpus runs, copied unchanged. The fork is
recorded live from the wrong run at its decision - the model call whose response
recorded the currency - with the right run's own recorded response to that call
substituted, and kept whatever the model did next. Nothing substituted is
written by hand.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from replay_events import EffectRequested, MemoryWrite, Result

from ..kernel import resolve, step_index
from .corpus import load_into


def load(store, directory: pathlib.Path) -> dict[str, Any]:
    """Load every committed canonical run into a store. Returns the manifest."""
    manifest = json.loads((directory / "manifest.json").read_text())
    known = {m.run_id for m in store.list_runs()}
    # Parents before children: a fork's metadata names a run that must exist.
    for path in sorted(directory.glob("*.json"), key=lambda p: (p.stem not in manifest["roots"], p.stem)):
        if path.name != "manifest.json" and path.stem not in known:
            load_into(store, path)
    return manifest


def decision_seq(events) -> int:
    """The seq of the model call that decided the currency.

    The response of that call is the one containing the record_invoice_field call
    whose write the conversion read - so forking there changes what the model
    decided, and everything the decision caused runs live afterwards.
    """
    currency = [e for e in events if isinstance(e, MemoryWrite) and e.key == "invoice.currency"]
    if not currency:
        raise ValueError("this run never recorded a currency")
    steps = step_index(events)
    step = steps[currency[-1].eid]
    tool = next(e for e in events if isinstance(e, EffectRequested) and steps[e.eid] == step)
    return max(e.seq for e in events
               if isinstance(e, EffectRequested) and e.effect.effect_kind == "model" and e.seq < tool.seq)


def recorded_currency_call(result_value) -> dict:
    """The record_invoice_field arguments a recorded model response carries."""
    for tool_use in tool_uses(result_value):
        if tool_use["name"] == "record_invoice_field":
            return tool_use["input"]
    raise ValueError("this response records no field")


def tool_uses(result_value) -> list[dict]:
    """The tool calls in a recorded model response, with their arguments parsed."""
    calls, current = [], None
    for chunk in result_value:
        start = chunk.get("contentBlockStart", {}).get("start", {}) if isinstance(chunk, dict) else {}
        if "toolUse" in start:
            current = {"name": start["toolUse"]["name"], "input": ""}
            calls.append(current)
        delta = chunk.get("contentBlockDelta", {}).get("delta", {}) if isinstance(chunk, dict) else {}
        if "toolUse" in delta and current is not None:
            current["input"] += delta["toolUse"].get("input", "")
    return [dict(call, input=json.loads(call["input"] or "{}")) for call in calls]


def substitution(store, wrong_run_id: str, right_run_id: str) -> tuple[int, Result]:
    """Where to fork the wrong run, and what to serve there: the right run's own
    recorded response at its decision, unmodified."""
    seq = decision_seq(resolve(store, wrong_run_id).events)
    right = resolve(store, right_run_id)
    return seq, Result(value=right.at(decision_seq(right.events)).result.value)
