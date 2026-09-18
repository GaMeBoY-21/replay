"""The frontend's fixtures, derived from the committed canonical runs."""

from __future__ import annotations

import pathlib
from typing import Any

from ..kernel import diff_runs, flag_output, resolve, trace_view
from ..projector import views as read_models
from .canonical import load


def fixtures(store, canonical: pathlib.Path) -> dict[str, Any]:
    manifest = load(store, canonical)
    out: dict[str, Any] = {"manifest": manifest}
    run_ids = [manifest[role]["run_id"] for role in ("wrong", "right", "fork", "halted") if role in manifest]
    for run_id in run_ids:
        events = resolve(store, run_id).events
        out[f"run-{run_id}"] = read_models.run_view(events, store.get_metadata(run_id))
        try:
            out[f"trace-{run_id}"] = trace_view(events, flag_output(events))
        except ValueError:
            pass  # a run that read no state has no output to trace
    out["diff-wrong-fork"] = diff_runs(store, manifest["wrong"]["run_id"], manifest["fork"]["run_id"])
    return out
