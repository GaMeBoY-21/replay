"""The projector: DynamoDB Streams in, rebuilt projections out.

Idempotent by reconstruction, not by bookkeeping. Streams are at-least-once and
give no ordering guarantee across shards, so incrementally mutating a view would
have to be correct under duplicate and out-of-order delivery. Instead a batch is
reduced to the set of runs it touched, and each is rebuilt from its full log. A
projection is a pure function of the log, so the same records - in any order,
any number of times - produce the same rows. The cost is one resolve per affected
run per batch.

This file is the adapter. `views.py` is the logic, and the tests live there.
"""

from __future__ import annotations

from typing import Any

from ..kernel.chain import lineage, resolve
from . import views as read_models


def affected_runs(stream_event: dict[str, Any]) -> list[str]:
    """The runs a batch touched, first-seen order, each once.

    Both event items and METADATA items count: a status change touches a run's
    summary as surely as a new event does.
    """
    seen: dict[str, None] = {}
    for record in stream_event.get("Records", []):
        keys = (record.get("dynamodb") or {}).get("Keys") or {}
        pk = (keys.get("PK") or {}).get("S", "")
        if pk.startswith("RUN#"):
            seen.setdefault(pk.removeprefix("RUN#"), None)
    return list(seen)


def project_run(store, views, run_id: str) -> dict[str, Any]:
    """Rebuild one run's summary and its place in its fork tree."""
    metadata = store.get_metadata(run_id)
    summary = read_models.summary(resolve(store, run_id).events, metadata)
    views.put_summary(run_id, summary)
    root, _ = lineage(store, run_id)[-1]
    views.put_tree_entry(root, run_id, {
        "run_id": run_id,
        "parent_run_id": metadata.parent_run_id,
        "forked_at_seq": metadata.forked_at_seq,
        "status": summary["status"],
    })
    return summary


def project(stream_event: dict[str, Any], store, views) -> dict[str, dict[str, Any]]:
    return {run_id: project_run(store, views, run_id) for run_id in affected_runs(stream_event)}


def rebuild_all(store, views) -> int:
    """Every run's projection from scratch - what the local server does at startup."""
    run_ids = [metadata.run_id for metadata in store.list_runs()]
    for run_id in run_ids:
        project_run(store, views, run_id)
    return len(run_ids)
