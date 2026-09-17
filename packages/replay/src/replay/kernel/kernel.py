"""The gate.

Every source of non-determinism in a run passes through here. The agent loop
either side of it is deterministic, so a run is fully described by the list of
values this function returned — which is the whole idea, and everything else in
the system is a consequence of it.

The gate is spelled as two halves rather than one function because a tool effect
opens and closes at different moments: the SDK surfaces a tool's result only
after the tool has already run. Both halves go through the same mode handling,
the same divergence detector and the same breakers, so there is exactly one
place where non-determinism enters the log.
"""

from __future__ import annotations

from dataclasses import dataclass

from replay_events import (
    BreakerTripped as BreakerTrippedEvent,
)
from replay_events import (
    Effect,
    EffectCompleted,
    EffectRequested,
    Result,
)

from .context import RunContext
from .errors import BreakerTripped, DivergenceError
from .modes import LiveMode


@dataclass
class Begun:
    """The outcome of claiming a seq.

    `live` is the flag, and nothing anywhere tests `result is None` instead. A
    recorded result is allowed to *be* `None`; a payload is data, never a
    control signal, and testing the payload re-executes the effect.
    """

    seq: int
    live: bool
    result: Result | None = None


def assert_same_shape(recorded: Effect, live: Effect) -> None:
    """The divergence detector.

    If a replay reaches step 7 and the agent asks for something other than what
    was recorded, the recording was not deterministic — an un-gated clock read,
    an un-gated random draw, a concurrently dispatched pair of tool calls. This
    is the only detector for all of them.

    It fails loudly and immediately on purpose. A replay that carries on past a
    divergence produces a plausible wrong answer, which is strictly worse than
    an exception: nobody debugs an answer that looks fine.
    """
    if recorded.shape() != live.shape():
        raise DivergenceError(
            "replay diverged from the recording\n"
            f"  recorded: {recorded.describe()}\n"
            f"      live: {live.describe()}\n"
            f"  recorded shape: {recorded.shape()}\n"
            f"      live shape: {live.shape()}"
        )


def serve_recorded(ctx: RunContext, seq: int, effect: Effect) -> Result:
    """Return what the log says this effect produced. Execute nothing."""
    record = ctx.log.at(seq)
    assert_same_shape(record.effect, effect)
    # Advance the counters without tripping. A resumed run that skipped this
    # would arrive at its halt point counting from zero and walk straight back
    # into the loop it was halted for.
    ctx.breakers.observe(effect, seq)
    result = record.result
    ctx.breakers.account(result)
    return result


def begin_effect(ctx: RunContext, effect: Effect) -> Begun:
    """Claim the seq, and either serve the effect or authorise executing it."""
    seq = ctx.next_seq()

    # ---- Replay: serve from the log. Execute nothing. ----
    if ctx.mode.kind == "replay" and seq <= ctx.mode.up_to:
        return Begun(seq, live=False, result=serve_recorded(ctx, seq, effect))

    # ---- Fork: replay the shared prefix, then substitute exactly once. ----
    if ctx.mode.kind == "fork":
        if seq < ctx.mode.at:
            return Begun(seq, live=False, result=serve_recorded(ctx, seq, effect))
        if seq == ctx.mode.at:
            assert_same_shape(ctx.log.at(seq).effect, effect)
            # Read the mutation out before the mode is replaced. `LiveMode` has
            # no `mutation` field, so reading it afterwards is an AttributeError
            # on the one step the entire fork exists to produce.
            mutation = ctx.mode.mutation
            # The fork OWNS this step: both events go to the fork's own log.
            # Were they left to the parent, the fork's shared prefix would
            # resolve to the parent's *original* value at exactly the step that
            # was replaced, and replaying the fork would serve a value the fork
            # never saw. A fork you cannot replay is a screenshot, not a run.
            ctx.append(EffectRequested(seq=seq, effect=effect, reads=ctx.snapshot_reads()))
            ctx.append(EffectCompleted(seq=seq, result=mutation, substituted=True))
            ctx.breakers.observe(effect, seq)
            ctx.breakers.account(mutation)
            ctx.mode = LiveMode()  # everything after this executes for real
            return Begun(seq, live=False, result=mutation)

    # ---- Live: guard, record the REQUEST, authorise execution. ----
    try:
        ctx.breakers.check(effect, seq)
    except BreakerTripped as trip:
        record_trip(ctx, seq, trip)
        # The seq stays claimed. A resume replays to here and retries this same
        # step — against a raised ceiling, or it trips again immediately.
        raise

    ctx.append(EffectRequested(seq=seq, effect=effect, reads=ctx.snapshot_reads()))
    return Begun(seq, live=True)


def will_execute(ctx: RunContext, seq: int) -> bool:
    """Whether `begin_effect` at this seq would authorise running the effect.

    False for a seq the log answers - replayed, a fork's shared prefix, or the
    fork's substituted step. A pre-check that trips a breaker on a step the log
    will serve would halt a replay for a ceiling the recording already passed.
    """
    if ctx.mode.kind == "replay" and seq <= ctx.mode.up_to:
        return False
    if ctx.mode.kind == "fork" and seq <= ctx.mode.at:
        return False
    return True


def record_trip(ctx: RunContext, seq: int, trip: BreakerTripped) -> int:
    """Append a breaker trip at a claimed seq. One place, whichever side tripped."""
    return ctx.append(BreakerTrippedEvent(seq=seq, breaker=trip.name, detail=trip.detail))


def complete_effect(ctx: RunContext, seq: int, result: Result) -> Result:
    """Close an effect `begin_effect` authorised. Called exactly once per seq."""
    ctx.append(EffectCompleted(seq=seq, result=result))
    ctx.breakers.account(result)
    return result


def perform(ctx: RunContext, effect: Effect, execute) -> Result:
    """The gate, for a synchronous effect."""
    begun = begin_effect(ctx, effect)
    if not begun.live:
        return begun.result
    return complete_effect(ctx, begun.seq, execute())


async def perform_async(ctx: RunContext, effect: Effect, execute) -> Result:
    """The gate, for an awaited effect.

    `async` is not concurrency. A single coroutine awaited to completion, one
    effect at a time, is exactly as deterministic as the synchronous path — and
    it is unavoidable, because the SDK's model interface is an async generator.
    What is banned is `gather`, task groups, threads and background tasks, where
    completion order is not reproducible.
    """
    begun = begin_effect(ctx, effect)
    if not begun.live:
        return begun.result
    return complete_effect(ctx, begun.seq, await execute())
