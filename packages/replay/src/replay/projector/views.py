"""The read models. Pure functions of a resolved log - no store, no I/O.

A projection is derived and disposable: if one is ever wrong, delete it and
rebuild it from the log. That is also what makes the projector idempotent - it
calls these on the full log of every run a batch touched, so the same input
always produces the same row.
"""

from __future__ import annotations

from typing import Any

from replay_events import (
    BreakerTripped,
    EffectCompleted,
    EffectRequested,
    Event,
    MemoryRead,
    MemoryWrite,
    RunMetadata,
)

from ..kernel.provenance import step_index, step_to_seq


def answer(events: list[Event]) -> str | None:
    """The run's answer, recovered from the log.

    It is stored nowhere. It is the text of the last model response, recorded as
    an ordered chunk list like every other model effect - reading it back out is
    a small proof that the log is enough to reconstruct the run.
    """
    models = {e.seq for e in events if isinstance(e, EffectRequested) and e.effect.effect_kind == "model"}
    for event in reversed(events):
        if isinstance(event, EffectCompleted) and event.seq in models and isinstance(event.result.value, list):
            text = "".join(
                chunk["contentBlockDelta"]["delta"].get("text", "")
                for chunk in event.result.value
                if isinstance(chunk, dict)
                and isinstance(chunk.get("contentBlockDelta"), dict)
                and isinstance(chunk["contentBlockDelta"].get("delta"), dict)
            )
            if text.strip():
                return text.strip()
    return None


def steps(events: list[Event]) -> list[dict[str, Any]]:
    """One row per step: the effect performed there and the memory it touched."""
    index = step_index(events)
    rows: dict[int, dict[str, Any]] = {}

    def row(step: int) -> dict[str, Any]:
        return rows.setdefault(step, {
            "step": step, "seq": None, "kind": None, "name": None, "substituted": False,
            "reads": [], "writes": [], "breaker": None,
        })

    for event in events:
        current = row(index[event.eid])
        if isinstance(event, EffectRequested):
            current["seq"] = event.seq
            current["kind"] = event.effect.effect_kind
            current["name"] = getattr(event.effect, "name", None) or event.effect.effect_kind
        elif isinstance(event, EffectCompleted):
            current["substituted"] = event.substituted
        elif isinstance(event, MemoryRead):
            current["reads"].append({"eid": event.eid, "key": event.key, "source": event.source})
        elif isinstance(event, MemoryWrite):
            current["writes"].append({"eid": event.eid, "key": event.key, "value": event.value,
                                      "tombstone": event.tombstone, "reads": list(event.reads)})
        elif isinstance(event, BreakerTripped):
            # A breaker stops an effect before it is requested, so the halted
            # step has no EffectRequested and would otherwise project as a blank
            # row - invisible at exactly the moment the run stopped. The trip is
            # what happened there, so it names the step.
            current["seq"] = event.seq if current["seq"] is None else current["seq"]
            current["kind"] = "breaker"
            current["name"] = event.breaker
            current["breaker"] = {"name": event.breaker, "detail": event.detail}
    # A step with nothing in it but its closing boundary is not shown.
    return [rows[s] for s in sorted(rows) if rows[s]["kind"] is not None or rows[s]["reads"] or rows[s]["writes"]]


def summary(events: list[Event], metadata: RunMetadata) -> dict[str, Any]:
    rows = steps(events)
    halted = next((r for r in rows if r["kind"] == "breaker"), None)
    return {
        "run_id": metadata.run_id,
        "status": metadata.status.value,
        "parent_run_id": metadata.parent_run_id,
        "forked_at_seq": metadata.forked_at_seq,
        "answer": answer(events),
        "step_count": len(rows),
        "effect_count": sum(1 for e in events if isinstance(e, EffectRequested)),
        "halted": None if halted is None else {"step": halted["step"], "seq": halted["seq"], **halted["breaker"]},
    }


def run_view(events: list[Event], metadata: RunMetadata) -> dict[str, Any]:
    return {
        "metadata": metadata.model_dump(mode="json"),
        "summary": summary(events, metadata),
        "step_to_seq": {str(step): seq for step, seq in step_to_seq(events).items()},
        "steps": steps(events),
    }
