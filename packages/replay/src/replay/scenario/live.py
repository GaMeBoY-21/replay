"""A real model, and reading an outcome back out of a recorded run.

The canonical runs are recorded from a real model, not authored. The provider is
a local Ollama model reached through its OpenAI-compatible endpoint with Strands'
llama.cpp provider, which needs no extra dependency. Bedrock is the same swap:
`build_agent(model=BedrockModel(...))`.

`classify` reads a run's outcome from its log alone - what currency the agent
recorded, what total the conversion wrote, whether it submitted - so the corpus
statistics can be recomputed by anyone from the committed runs.
"""

from __future__ import annotations

import os
from typing import Any

from strands import Agent

from replay_events import EffectCompleted, EffectRequested, MemoryWrite, RunMetadata

from ..kernel import step_index
from .data import SYSTEM_PROMPT
from .tools import TOOLS

OLLAMA_URL = os.environ.get("REPLAY_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("REPLAY_OLLAMA_MODEL", "qwen2.5:14b")

RIGHT_TOTAL = 492.0
WRONG_TOTAL = 41000.0


def ollama_model(model_id: str = OLLAMA_MODEL, base_url: str = OLLAMA_URL):
    from strands.models.llamacpp import LlamaCppModel

    # No sampling parameters are set: the model runs at its own defaults.
    return LlamaCppModel(base_url=base_url, model_id=model_id, timeout=600.0)


def build_agent(model=None) -> Agent:
    return Agent(
        model=model if model is not None else ollama_model(),
        tools=list(TOOLS),
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


def answer_text(events) -> str | None:
    """The text of the last model response in the run, if it had any."""
    for event in reversed(events):
        if isinstance(event, EffectCompleted) and isinstance(event.result.value, list):
            text = "".join(
                chunk["contentBlockDelta"]["delta"].get("text", "")
                for chunk in event.result.value
                if isinstance(chunk, dict) and "contentBlockDelta" in chunk
                and isinstance(chunk["contentBlockDelta"].get("delta"), dict)
            )
            if text.strip():
                return text.strip()
    return None


def classify(events, metadata: RunMetadata) -> dict[str, Any]:
    """What happened in a recorded run, read from the run's own log."""
    steps = step_index(events)
    writes = [e for e in events if isinstance(e, MemoryWrite)]
    currency_writes = [e for e in writes if e.key == "invoice.currency"]
    totals = [e for e in writes if e.key == "report.total"]
    basis = {e.eid: e.value for e in writes if e.key == "invoice.currency.basis"}
    tool_calls = [e.effect.name for e in events if isinstance(e, EffectRequested) and e.effect.effect_kind == "tool"]

    by_eid = {e.eid: e for e in events}
    used = None
    if totals:
        # The currency write the final conversion actually read.
        for read_eid in totals[-1].reads:
            read = by_eid.get(read_eid)
            if getattr(read, "key", None) == "invoice.currency" and read.source is not None:
                used = by_eid[read.source]

    amount = totals[-1].value.get("amount") if totals and isinstance(totals[-1].value, dict) else None
    if metadata.status.value != "completed":
        outcome = "failed"
    elif amount is not None and abs(amount - RIGHT_TOTAL) < 0.005:
        outcome = "right"
    elif amount is not None:
        # Converted from some currency other than the invoice's: 41000 from USD,
        # but also 29930 from CAD or 44280 from EUR. Any total but the right one.
        outcome = "wrong"
    else:
        outcome = "failed"

    basis_text = None
    if used is not None:
        basis_text = next((v for eid, v in sorted(basis.items()) if eid > used.eid), None)

    return {
        "run_id": metadata.run_id,
        "status": metadata.status.value,
        "outcome": outcome,
        "steps": max(steps.values(), default=0),
        "effects": sum(1 for e in events if isinstance(e, EffectRequested)),
        "currencies_recorded": [e.value for e in currency_writes],
        "currency_used": None if used is None else used.value,
        "assumption_step": None if used is None else steps[used.eid],
        "assumption_basis": basis_text,
        "converted_total": amount,
        "submitted": any(e.key == "report.submitted" for e in writes),
        "fetched_remittance": "get_remittance_details" in tool_calls,
        "vendor_lookups": tool_calls.count("lookup_vendor"),
        "answer": answer_text(events),
    }
