"""The corpus: every run of the task recorded from a real model, and its statistics.

Each run is committed as JSON - its metadata and its events - and the statistics
are recomputed from those files alone, so the failure rate is a number anyone can
check rather than a claim.

Two readings of each run, because they measure different things:

- **worksheet**: what the agent recorded and converted - the currency it wrote to
  state and the total the conversion wrote. Only this is visible to the trace.
- **answer**: what the agent said in its final response. An agent can assert a
  currency in prose without ever recording it.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter
from typing import Any

from replay_events import RunMetadata, dump_event, parse_event

from .live import RIGHT_TOTAL, WRONG_TOTAL, classify


NOT_RUNS = {"stats.json", "provider.json"}


def run_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """The recorded runs in a corpus directory, and nothing else in it."""
    return sorted(p for p in directory.glob("*.json") if p.name not in NOT_RUNS)


def export_run(store, run_id: str, directory: pathlib.Path) -> pathlib.Path:
    path = directory / f"{run_id}.json"
    body = {
        "metadata": store.get_metadata(run_id).model_dump(mode="json"),
        "events": [dump_event(event) for event in store.read(run_id)],
    }
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return path


def load_run(path: pathlib.Path) -> tuple[RunMetadata, list]:
    body = json.loads(path.read_text())
    return RunMetadata.model_validate(body["metadata"]), [parse_event(e) for e in body["events"]]


def load_into(store, path: pathlib.Path) -> str:
    """Put a committed run into a store, as recorded."""
    metadata, events = load_run(path)
    store.put_metadata(metadata)
    for event in events:
        store.append(metadata.run_id, event)
    return metadata.run_id


def answer_outcome(answer: str | None) -> str:
    """right / wrong / unclear, from the final response's text alone.

    Right names the true converted total. Wrong reports a total in the tens of
    thousands - the untranslated rupee amount presented as dollars, however the
    arithmetic came out.
    """
    if not answer:
        return "unclear"
    numbers = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", answer)]
    if any(abs(n - RIGHT_TOTAL) < 0.005 for n in numbers):
        return "right"
    if any(10_000 <= n <= 100_000 for n in numbers):
        return "wrong"
    return "unclear"


def summarise(paths: list[pathlib.Path]) -> dict[str, Any]:
    rows = []
    for path in sorted(paths):
        metadata, events = load_run(path)
        row = classify(events, metadata)
        row["worksheet_outcome"] = row.pop("outcome")
        row["answer_outcome"] = answer_outcome(row["answer"])
        if metadata.status.value != "completed":
            row["overall"] = "failed"
        elif row["worksheet_outcome"] in ("right", "wrong"):
            row["overall"] = row["worksheet_outcome"]
        elif row["answer_outcome"] in ("right", "wrong"):
            row["overall"] = row["answer_outcome"]
        else:
            row["overall"] = "failed"
        rows.append(row)

    wrong_with_write = [r for r in rows if r["worksheet_outcome"] == "wrong"]
    return {
        "runs": len(rows),
        "overall": dict(Counter(r["overall"] for r in rows)),
        "worksheet": dict(Counter(r["worksheet_outcome"] for r in rows)),
        "answer": dict(Counter(r["answer_outcome"] for r in rows)),
        "steps": sorted(r["steps"] for r in rows),
        "assumption_steps_in_wrong_runs": sorted(r["assumption_step"] for r in wrong_with_write),
        "fetched_remittance": sum(r["fetched_remittance"] for r in rows),
        "vendor_lookups": sorted(r["vendor_lookups"] for r in rows),
        "rows": rows,
    }


def wrong_total() -> float:
    return WRONG_TOTAL
