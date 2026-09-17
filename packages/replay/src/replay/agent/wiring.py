"""Put a Strands agent through the gate.

Four changes, made to an agent that has already been constructed.
"""

from __future__ import annotations

from dataclasses import dataclass

from strands import Agent
from strands.tools.executors import SequentialToolExecutor

from replay_events import MemoryWrite

from ..kernel import RecordingState, RunContext
from ..kernel.chain import first_eid_at
from .hooks import ReplayHooks
from .model import ReplayModel


@dataclass
class Seam:
    model: ReplayModel
    hooks: ReplayHooks
    state: RecordingState


def seed_state(inner, events, before_eid: int | None = None) -> None:
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
        if before_eid is not None and event.eid >= before_eid:
            break
        if event.tombstone:
            inner.delete(event.key)
        else:
            inner.set(event.key, event.value)


def writer_index(events, before_eid: int | None = None) -> dict[str, int]:
    """memory key -> the eid of the write that last produced it, before a cut."""
    writers: dict[str, int] = {}
    for event in events:
        if before_eid is not None and event.eid >= before_eid:
            break
        if isinstance(event, MemoryWrite):
            writers[event.key] = event.eid
    return writers


def own_log_begins(ctx: RunContext) -> int | None:
    """The eid in the handed log where this run stops reading and starts owning.

    For a fork, the first event at the forked seq; for a resume, the first event
    at the seq after the replayed prefix - the halt. None for a full replay,
    which reads the whole log.
    """
    if ctx.log is None:
        return None
    if ctx.mode.kind == "fork":
        return first_eid_at(ctx.log.events, ctx.mode.at)
    if ctx.mode.kind == "replay":
        return first_eid_at(ctx.log.events, ctx.mode.up_to + 1)
    return None


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
    if ctx.mode.kind in ("replay", "fork") and ctx.log is not None:
        # As of the point this run's own events begin, not the end of the log it
        # was handed. A fork seeded with its parent's final state would read
        # values the parent wrote AFTER the step the fork replaced.
        cut = own_log_begins(ctx)
        seed_state(inner, ctx.log.events, before_eid=cut)
        # The trace's reverse index, to the same cut: a read in the fork must
        # point at the write it actually saw, not one the parent made later.
        ctx.writer_of = writer_index(ctx.log.events, before_eid=cut)
    # Wrapped AFTER construction. AgentState.get takes no default argument, and
    # RecordingState.get forwards none.
    state = RecordingState(ctx, inner)
    agent.state = state

    return Seam(model=model, hooks=hooks, state=state)
