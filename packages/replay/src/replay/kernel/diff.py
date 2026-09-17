"""Two runs, aligned on the seq axis.

When one run is a fork of the other, its prefix is shared **because it is
shared**: the fork stores nothing before its fork point and reads that prefix
from its parent. The diff reports the sharing from the lineage, not by comparing
content and reconstructing it. Two runs with no common lineage are compared by
content, and the report says which basis it used.
"""

from __future__ import annotations

from typing import Any

from replay_events import canonical

from .chain import lineage, resolve
from .log import EffectRecord


def _storage_prefix(store, a_id: str, b_id: str) -> int | None:
    """The seq before which one run reads its events from the other, if it does."""
    for child, parent in ((b_id, a_id), (a_id, b_id)):
        chain = lineage(store, child)
        cut = None
        for run_id, metadata in chain:
            if run_id == parent:
                return cut
            if metadata.forked_at_seq is not None:
                cut = metadata.forked_at_seq if cut is None else min(cut, metadata.forked_at_seq)
    return None


def _describe(record: EffectRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "effect": record.effect.describe(),
        "shape": record.effect.shape(),
        "result": None if record.completed is None else canonical(record.completed.result.model_dump(mode="json")),
        "substituted": bool(record.completed and record.completed.substituted),
    }


def diff_runs(store, a_id: str, b_id: str) -> dict[str, Any]:
    a, b = resolve(store, a_id), resolve(store, b_id)
    storage = _storage_prefix(store, a_id, b_id)

    rows = []
    divergence = None
    for seq in sorted(set(a.by_seq) | set(b.by_seq)):
        left, right = _describe(a.by_seq.get(seq)), _describe(b.by_seq.get(seq))
        same = left is not None and right is not None and (
            left["shape"], left["result"], left["substituted"]
        ) == (right["shape"], right["result"], right["substituted"])
        if divergence is None and not same:
            divergence = seq
        shared = storage is not None and seq < storage
        if shared and not same:
            raise AssertionError(
                f"seq {seq} is read from shared storage but differs between {a_id} and {b_id}"
            )
        rows.append({"seq": seq, "a": left, "b": right, "same": same, "shared": shared})

    if storage is not None:
        shared_prefix, basis = storage, "storage"
    else:
        shared_prefix = divergence if divergence is not None else len(rows)
        basis = "content"
    return {
        "a": a_id,
        "b": b_id,
        "shared_prefix": shared_prefix,
        "shared_by": basis,
        "divergence_seq": divergence,
        "rows": rows,
    }
