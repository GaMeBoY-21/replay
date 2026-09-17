"""Put a Strands agent through the gate.

Four changes, made to an agent that has already been constructed.
"""

from __future__ import annotations

from dataclasses import dataclass

from strands import Agent
from strands.tools.executors import SequentialToolExecutor

from replay_events import MemoryWrite

from ..kernel import RecordingState, RunContext
from .hooks import ReplayHooks
from .model import ReplayModel


@dataclass
class Seam:
    model: ReplayModel
    hooks: ReplayHooks
    state: RecordingState


def seed_state(inner, events, up_to_eid: int | None = None) -> None:
    """Apply recorded writes to the raw state, before a replay starts.

    This is the layer where seeding is correct, and it is the opposite of the
    rule in `state_at`'s docstring on purpose. There, writes re-execute as the
    run proceeds, so seeding up front would show a step a value it has not
    written yet. Here the tools are replaced by recorded stand-ins and genuinely
    do not run, so nothing would ever write the state back; without this, a
    replayed agent finishes with the state it started with.

    Writes are applied in log order, tombstones included, straight to the inner
    state rather than through `RecordingState`, so seeding records nothing.
    """
    for event in events:
        if not isinstance(event, MemoryWrite):
            continue
        if up_to_eid is not None and event.eid > up_to_eid:
            break
        if event.tombstone:
            inner.delete(event.key)
        else:
            inner.set(event.key, event.value)


def attach(agent: Agent, ctx: RunContext) -> Seam:
    model = ReplayModel(ctx, agent.model)
    agent.model = model

    hooks = ReplayHooks(ctx)
    agent.hooks.add_hook(hooks)

    # The default ConcurrentToolExecutor runs a response's tool calls under
    # asyncio.gather. Completion order is not reproducible, so a response asking
    # for two tools gets its seqs in whatever order they finished - wrong at
    # record time, and then replayed faithfully forever.
    agent.tool_executor = SequentialToolExecutor()

    inner = agent.state
    if ctx.mode.kind == "replay" and ctx.log is not None:
        seed_state(inner, ctx.log.events)
    # Wrapped AFTER construction. AgentState.get takes no default argument, and
    # RecordingState.get forwards none.
    state = RecordingState(ctx, inner)
    agent.state = state

    return Seam(model=model, hooks=hooks, state=state)
