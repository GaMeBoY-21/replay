"""A real Strands agent with a scripted provider and a side-effect witness.

The agent is a real `strands.Agent`: the real event loop, real hooks, a real
tool executor, real `agent.state`. Only the model is scripted, so the suite
never needs Bedrock.

The witness is module-level on purpose. A tool's return value can match a
recording by coincidence; a counter the real tool function increments cannot.
If replay ever lets a real tool run, `TOOL_EXECUTIONS` moves.
"""

from __future__ import annotations

import json
from typing import Any

from strands import Agent, tool
from strands.models import Model

from replay.agent import attach
from replay.kernel import (
    BreakerConfig,
    Breakers,
    LiveMode,
    ReplayMode,
    RunContext,
    load_log,
)
from replay.store import MemoryLogStore

TOOL_EXECUTIONS = 0
MODEL_CALLS = 0

PROMPT = "Reconcile invoice INV-2291 and report the total in USD."
SYSTEM_PROMPT = "You reconcile invoices. Record what you learn in agent state."


def reset_witnesses() -> None:
    global TOOL_EXECUTIONS, MODEL_CALLS
    TOOL_EXECUTIONS = 0
    MODEL_CALLS = 0


def _executed() -> None:
    global TOOL_EXECUTIONS
    TOOL_EXECUTIONS += 1


# ------------------------------------------------------------------ tools


@tool(context=True)
def read_invoice_header(invoice_id: str, tool_context) -> dict:
    """Read an invoice header and remember its currency."""
    _executed()
    # The poisoned value lives in agent.state, where the provenance trace can
    # see which step wrote it. In agent.messages every step reads everything.
    tool_context.agent.state.set("invoice.currency", "USD")
    return {"invoice_id": invoice_id, "currency": "USD"}


@tool(context=True)
def convert_currency(amount: float, tool_context) -> dict:
    """Convert an amount into USD at the recorded rate."""
    _executed()
    currency = tool_context.agent.state.get("invoice.currency")
    rate = 1.0 if currency == "USD" else 0.012
    tool_context.agent.state.set("fx.rate", rate)
    return {"converted": amount * rate, "rate": rate}


@tool(context=True)
def record_total(total: float, tool_context) -> str:
    """Record the reconciled total."""
    _executed()
    tool_context.agent.state.get("fx.rate")
    tool_context.agent.state.set("report.total", total)
    return "recorded"


TOOLS = [read_invoice_header, convert_currency, record_total]


# ------------------------------------------------------------------ chunks


def tool_use(name: str, arguments: dict[str, Any], tool_use_id: str) -> list[dict[str, Any]]:
    return [
        {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": tool_use_id}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}},
        {"contentBlockStop": {}},
    ]


# Usage rides on the metadata chunk, as it does from a real provider. Every
# response carries one, so the budget breaker counts real recorded runs.
USAGE = {"metadata": {"usage": {"inputTokens": 40, "outputTokens": 9, "totalTokens": 49},
                      "metrics": {"latencyMs": 12}}}


def tool_response(*calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = [{"messageStart": {"role": "assistant"}}]
    for call in calls:
        chunks.extend(call)
    chunks.append({"messageStop": {"stopReason": "tool_use"}})
    chunks.append(USAGE)
    return chunks


def text_response(text: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": text}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
        USAGE,
    ]


# Two tool calls in the first response, so the executor's ordering is exercised.
SCRIPT = [
    tool_response(
        tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, "tooluse-1"),
        tool_use("convert_currency", {"amount": 41000}, "tooluse-2"),
    ),
    tool_response(tool_use("record_total", {"total": 41000}, "tooluse-3")),
    text_response("Total: $41,000.00"),
]


class ScriptedModel(Model):
    """Answers by position in the conversation: the Nth response for the Nth
    assistant turn. Deterministic by construction."""

    def __init__(self, script: list[list[dict[str, Any]]] | None = None) -> None:
        self.script = script if script is not None else SCRIPT

    def get_config(self) -> dict[str, Any]:
        return {}

    def update_config(self, **model_config: Any) -> None:
        pass

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        global MODEL_CALLS
        MODEL_CALLS += 1
        turn = sum(1 for message in messages if message["role"] == "assistant")
        for chunk in self.script[min(turn, len(self.script) - 1)]:
            yield chunk


class RefusingModel(ScriptedModel):
    """For replay. If replay ever calls the provider, the run fails here."""

    async def stream(self, *args, **kwargs):
        raise AssertionError("the provider was called during replay")
        yield


# ------------------------------------------------------------------ runs


def build_agent(model: Model | None = None, tools=None, hooks=None) -> Agent:
    return Agent(
        model=model if model is not None else ScriptedModel(),
        tools=list(TOOLS if tools is None else tools),
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
        hooks=list(hooks or []),
    )


def unbounded() -> Breakers:
    return Breakers(BreakerConfig(max_repeats=10**9, max_effects=10**9, max_tokens=10**9))


def record(run_id: str = "strands-run", *, model=None, prompt: str = PROMPT, breakers=None, tools=None):
    """Record a live run. Returns (store, agent, answer)."""
    store = MemoryLogStore()
    ctx = RunContext(run_id=run_id, store=store, mode=LiveMode(), breakers=breakers or unbounded())
    agent = build_agent(model=model, tools=tools)
    attach(agent, ctx)
    answer = str(agent(prompt))
    return store, agent, answer


def replay(store, run_id: str = "strands-run", *, model=None, prompt: str = PROMPT):
    """Replay a recorded run into a throwaway store. Returns (replay_store, agent, answer)."""
    log = load_log(store, run_id)
    replay_store = MemoryLogStore()
    ctx = RunContext(
        run_id=f"{run_id}-replay",
        store=replay_store,
        mode=ReplayMode(up_to=log.max_seq),
        log=log,
        breakers=unbounded(),
    )
    agent = build_agent(model=model if model is not None else RefusingModel())
    attach(agent, ctx)
    answer = str(agent(prompt))
    return replay_store, agent, answer


def raw_state(agent: Agent) -> dict[str, Any]:
    """The state underneath the recorder, read without recording a read."""
    return agent.state._inner.get()


def root_cause(exc: BaseException) -> BaseException:
    """The SDK may wrap an exception raised inside the event loop."""
    seen = exc
    while True:
        nxt = seen.__cause__ or seen.__context__
        if nxt is None:
            return seen
        seen = nxt
