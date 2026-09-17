"""The seams, one property at a time."""

from __future__ import annotations

from backends import new_store
import asyncio
import json

import pytest
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider
from strands.tools.executors import SequentialToolExecutor

import strands_harness as H
from replay.agent import RecordedTool, ReplayHooks, ReplayModel, attach
from replay.kernel import (
    BreakerConfig,
    Breakers,
    LiveMode,
    ReplayMode,
    RunContext,
    begin_effect,
    complete_effect,
    load_log,
)
from replay.store import MemoryLogStore
from replay_events import MemoryRead, MemoryWrite, Result, ToolEffect, dump_event


def drain(model, *args, **kwargs):
    async def collect():
        return [chunk async for chunk in model.stream(*args, **kwargs)]

    return asyncio.run(collect())


# ------------------------------------------------------------------ the model seam


def test_the_model_effect_carries_all_five_inputs():
    store = new_store()
    ctx = RunContext("model", store, LiveMode(), breakers=H.unbounded())
    model = ReplayModel(ctx, H.ScriptedModel([H.text_response("hi")]))

    messages = [{"role": "user", "content": [{"text": "hello"}]}]
    specs = [{"name": "lookup", "description": "d", "inputSchema": {"json": {}}}]
    choice = {"tool": {"name": "lookup"}}
    content = [{"text": "be brief"}]
    drain(model, messages, specs, "be brief", tool_choice=choice, system_prompt_content=content)

    (requested,) = [e for e in store.read("model") if e.type == "EffectRequested"]
    effect = requested.effect
    assert effect.messages == messages
    assert effect.tool_specs == specs
    assert effect.system_prompt == "be brief"
    assert effect.tool_choice == choice
    assert effect.system_prompt_content == content


def test_one_model_call_is_one_effect_whatever_the_chunk_count():
    store = new_store()
    ctx = RunContext("chunks", store, LiveMode(), breakers=H.unbounded())
    response = H.text_response("a long answer")
    model = ReplayModel(ctx, H.ScriptedModel([response]))

    chunks = drain(model, [{"role": "user", "content": [{"text": "hi"}]}])

    assert len(chunks) == len(response) > 1
    events = store.read("chunks")
    assert [e.type for e in events if e.type.startswith("Effect")] == ["EffectRequested", "EffectCompleted"]
    assert events[1].result.value == response


def test_the_volatile_and_per_cycle_inputs_never_reach_the_log():
    """invocation_state gets a fresh uuid4 every cycle, and AfterToolCallEvent's
    duration comes from a clock. Neither may be recorded."""
    store, _, _ = H.record()
    dumped = json.dumps([dump_event(e) for e in store.read("strands-run")])
    for leaked in ("event_loop_cycle_id", "invocation_state", "cancel_signal",
                   "agent_metadata", "duration", "tracking_id"):
        assert leaked not in dumped, f"{leaked} reached the replayable log"


def test_structured_output_is_gated():
    ctx = RunContext("so", new_store(), LiveMode())
    model = ReplayModel(ctx, H.ScriptedModel())
    with pytest.raises(NotImplementedError):
        model.structured_output(dict, [{"role": "user", "content": [{"text": "x"}]}])


def test_a_changed_tool_choice_diverges_on_replay():
    """The fingerprint is compared through the seam, not only in the contract."""
    from replay.kernel import DivergenceError

    store = new_store()
    live = RunContext("choice", store, LiveMode(), breakers=H.unbounded())
    messages = [{"role": "user", "content": [{"text": "hi"}]}]
    drain(ReplayModel(live, H.ScriptedModel([H.text_response("x")])), messages,
          tool_choice={"auto": {}})

    log = load_log(store, "choice")
    replaying = RunContext("choice-replay", new_store(), ReplayMode(up_to=log.max_seq), log=log)
    with pytest.raises(DivergenceError):
        drain(ReplayModel(replaying, H.RefusingModel()), messages, tool_choice={"any": {}})


# ------------------------------------------------------------------ the tool seam


def recorded_tool_log():
    """A log holding one completed tool effect."""
    store = new_store()
    ctx = RunContext("tool", store, LiveMode(), breakers=H.unbounded())
    effect = ToolEffect(name="record_total", arguments={"total": 41000}, tool_use_id="tooluse-9")
    begun = begin_effect(ctx, effect)
    recorded = {"toolUseId": "tooluse-9", "status": "success", "content": [{"text": "recorded"}]}
    complete_effect(ctx, begun.seq, Result(value=recorded))
    return load_log(store, "tool"), recorded


def test_the_replay_stub_yields_a_bare_tool_result():
    log, recorded = recorded_tool_log()
    ctx = RunContext("tool-replay", new_store(), ReplayMode(up_to=log.max_seq), log=log)
    hooks = ReplayHooks(ctx)
    event = BeforeToolCallEvent(
        agent=None,
        selected_tool=H.record_total,
        tool_use={"name": "record_total", "input": {"total": 41000}, "toolUseId": "tooluse-9"},
        invocation_state={},
    )

    hooks._before_tool(event)

    assert isinstance(event.selected_tool, RecordedTool)

    async def collect():
        return [item async for item in event.selected_tool.stream(event.tool_use, {})]

    yielded = asyncio.run(collect())
    assert yielded == [recorded]
    assert "toolResult" not in yielded[0], "a wrapped result double-nests at the next model call"


def test_attach_replaces_the_concurrent_executor():
    ctx = RunContext("exec", new_store(), LiveMode())
    agent = H.build_agent()
    attach(agent, ctx)
    assert isinstance(agent.tool_executor, SequentialToolExecutor)


def test_two_tools_in_one_response_record_in_the_order_the_model_asked():
    store, _, _ = H.record()
    names = [e.effect.name for e in store.read("strands-run")
             if e.type == "EffectRequested" and e.effect.effect_kind == "tool"]
    assert names[:2] == ["read_invoice_header", "convert_currency"]


class RetryOnce(HookProvider):
    def __init__(self) -> None:
        self.retried = False

    def register_hooks(self, registry, **kwargs):
        registry.add_callback(AfterToolCallEvent, self.after)

    def after(self, event):
        if not self.retried:
            self.retried = True
            event.retry = True


def test_a_tool_retry_opens_a_new_seq_and_closes_each_once():
    store = new_store()
    ctx = RunContext("retry", store, LiveMode(), breakers=H.unbounded())
    agent = H.build_agent(hooks=[RetryOnce()])
    attach(agent, ctx)
    H.reset_witnesses()

    agent(H.PROMPT)

    events = store.read("retry")
    tools = [e for e in events if e.type == "EffectRequested" and e.effect.effect_kind == "tool"]
    assert [e.effect.name for e in tools][:2] == ["read_invoice_header", "read_invoice_header"]
    assert tools[0].seq != tools[1].seq
    # load_log refuses a seq closed twice, so loading is the assertion.
    log = load_log(store, "retry")
    assert log.incomplete == []
    assert H.TOOL_EXECUTIONS == 4


# ------------------------------------------------------------------ state


def test_state_reads_and_writes_land_in_the_log_with_their_step():
    store, _, _ = H.record()
    events = store.read("strands-run")
    writes = {e.key: e for e in events if isinstance(e, MemoryWrite)}
    reads = {e.eid: e for e in events if isinstance(e, MemoryRead)}

    assert set(writes) == {"invoice.currency", "fx.rate", "report.total"}
    assert writes["invoice.currency"].value == "USD"
    # convert_currency read the currency, then wrote the rate, in one step.
    (read_eid,) = writes["fx.rate"].reads
    assert reads[read_eid].key == "invoice.currency"
    assert reads[read_eid].source == writes["invoice.currency"].eid


def test_replay_seeds_state_it_does_not_rebuild():
    """Tools are stubbed during replay, so nothing writes state back. Without
    seeding from the log, the replayed agent ends with empty state."""
    store, _, _ = H.record()
    _, agent, _ = H.replay(store)
    assert H.raw_state(agent)["report.total"] == 41000


# ------------------------------------------------------------------ breakers


LOOP = [
    H.tool_response(H.tool_use("read_invoice_header", {"invoice_id": "INV-2291"}, f"loop-{n}"))
    for n in range(10)
]


def test_a_tool_breaker_halts_the_run_before_the_tool_runs():
    store = new_store()
    ctx = RunContext("loop", store, LiveMode(), breakers=Breakers(BreakerConfig(max_repeats=3)))
    agent = H.build_agent(model=H.ScriptedModel(LOOP))
    attach(agent, ctx)
    H.reset_witnesses()

    answer = str(agent(H.PROMPT))

    assert "halted by the loop breaker" in answer
    assert H.TOOL_EXECUTIONS == 2, "the third identical call must not execute"
    events = store.read("loop")
    trips = [e for e in events if e.type == "BreakerTripped"]
    assert len(trips) == 1 and trips[0].breaker == "loop"
    requested = {e.seq for e in events if e.type == "EffectRequested"}
    assert trips[0].seq not in requested, "the halted effect was never requested"
