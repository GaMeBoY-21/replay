"""The canonical runs: chosen from the corpus, not authored.

    fixtures/canonical/
      manifest.json      which run plays which role, and why it was chosen
      <run_id>.json      each run, as recorded - metadata and events

The wrong run and the right run are corpus runs, copied unchanged. The fork is
recorded live from the wrong run, at its currency conversion, with the real
rate and a warning substituted - and kept whatever the model did next.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from replay_events import EffectRequested, MemoryWrite, Result

from ..kernel import resolve, step_index
from .corpus import load_into

# What the conversion would have returned had the source currency been right.
SUBSTITUTED = {"from": "INR", "to": "USD", "rate": 0.012, "total": 41000, "converted": 492.0,
               "warning": "source currency INR, not USD"}


def load(store, directory: pathlib.Path) -> dict[str, Any]:
    """Load every committed canonical run into a store. Returns the manifest."""
    manifest = json.loads((directory / "manifest.json").read_text())
    known = {m.run_id for m in store.list_runs()}
    # Parents before children: a fork's metadata names a run that must exist.
    for path in sorted(directory.glob("*.json"), key=lambda p: (p.stem not in manifest["roots"], p.stem)):
        if path.name != "manifest.json" and path.stem not in known:
            load_into(store, path)
    return manifest


def conversion_seq(events) -> int:
    """The seq of the conversion whose result became the reported total."""
    steps = step_index(events)
    totals = [e for e in events if isinstance(e, MemoryWrite) and e.key == "report.total"]
    if not totals:
        raise ValueError("this run never converted a total")
    step = steps[totals[-1].eid]
    for event in events:
        if isinstance(event, EffectRequested) and steps[event.eid] == step:
            return event.seq
    raise ValueError("no effect at the step that wrote the total")


def substitution(store, run_id: str) -> tuple[int, Result]:
    """The conversion's seq, and a result replacing the recorded one there."""
    log = resolve(store, run_id)
    seq = conversion_seq(log.events)
    recorded = log.at(seq).result.value
    return seq, Result(value=dict(recorded, content=[{"text": json.dumps(SUBSTITUTED)}]))
