"""A scripted agent, and the poison that proves replay executes nothing.

The harness is deliberately not the Strands integration. It is a deterministic
agent loop that does exactly what the real one does through the gate — read
state, perform an effect, write state, close the step — so the kernel can be
tested before any SDK is involved.

Writes re-execute here. That is the honest shape for a harness, and it is why
replayed state is rebuilt as the run proceeds rather than seeded from the log up
front. See `state_at`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from replay import kernel as K
from replay.kernel import (
    BreakerConfig,
    Breakers,
    DictState,
    LiveMode,
    RecordingState,
    ReplayMode,
    RunContext,
    load_log,
    perform,
)
from replay.store import MemoryLogStore
from replay_events import (
    ClockEffect,
    Effect,
    ModelEffect,
    RandomEffect,
    Result,
    ToolEffect,
)


class ExecutedDuringReplay(AssertionError):
    """Raised by the poisoned executor. Never caught inside the harness."""


def poisoned_executor(seq: int, step: "Step"):
    raise ExecutedDuringReplay(
        f"execute() ran during replay at seq {seq} ({step.kind}:{step.name}). "
        "Replay must serve from the log and execute nothing."
    )


@dataclass(frozen=True)
class Step:
    kind: str = "tool"
    name: str = "lookup"
    args: dict[str, Any] = field(default_factory=dict)
    reads: tuple[str, ...] = ()
    writes: str | None = None


def effect_for(step: Step) -> Effect:
    if step.kind == "tool":
        return ToolEffect(name=step.name, arguments=dict(step.args))
    if step.kind == "model":
        system = step.args.get("system")
        return ModelEffect(
            messages=[{"role": "user", "content": step.name}],
            # The contract types this as a string. Coercing here keeps the
            # generated scripts wide without weakening the contract.
            system_prompt=None if system is None else str(system),
        )
    if step.kind == "clock":
        return ClockEffect(label=step.name)
    if step.kind == "random":
        return RandomEffect(method=step.name or "uuid4")
    raise ValueError(f"unknown step kind {step.kind!r}")


def live_executor(seq: int, step: Step) -> Result:
    """Stands in for a real, non-deterministic outside world.

    The value depends on the seq, so a run that drifted by even one effect
    produces different state — which is what makes the determinism test able to
    see drift at all.
    """
    return Result(value={"seq": seq, "from": step.name, "kind": step.kind})


@dataclass
class Outcome:
    run_id: str
    store: MemoryLogStore
    state: dict[str, Any]
    served: list[Any]


def run_script(
    script: list[Step],
    *,
    run_id: str,
    store: MemoryLogStore,
    mode=None,
    log=None,
    executor=live_executor,
    initial: dict[str, Any] | None = None,
    breakers=None,
) -> Outcome:
    ctx = RunContext(
        run_id=run_id, store=store, mode=mode or LiveMode(), log=log, breakers=breakers
    )
    inner = DictState(initial)
    state = RecordingState(ctx, inner)
    served: list[Any] = []

    # One effect per step, so the step index is the effect seq. The harness
    # tracks it itself rather than reaching into the context: `execute` takes no
    # arguments by design, because the gate must not hand its internals to the
    # thing it is gating.
    for seq, step in enumerate(script):
        for key in step.reads:
            state.get(key)
        result = perform(ctx, effect_for(step), lambda s=step, n=seq: executor(n, s))
        served.append(result.value)
        if step.writes:
            state.set(step.writes, result.value)
        ctx.step_boundary()

    return Outcome(run_id=run_id, store=store, state=inner.get(), served=served)


def record(script: list[Step], *, run_id: str = "run-1", initial=None, breakers=None) -> Outcome:
    return run_script(
        script,
        run_id=run_id,
        store=MemoryLogStore(),
        executor=live_executor,
        initial=initial,
        breakers=breakers,
    )


def unbounded() -> Breakers:
    """Breakers that never trip.

    The determinism property test generates repeated identical effects by
    construction, which is exactly what the loop breaker exists to stop. Letting
    it fire there would test the breaker, not determinism; the breakers have
    their own tests.
    """
    return Breakers(BreakerConfig(max_repeats=10**9, max_effects=10**9, max_tokens=10**9))


def replay(
    recorded: Outcome,
    script: list[Step],
    *,
    initial=None,
    executor=poisoned_executor,
    breakers=None,
) -> Outcome:
    """Replay a recorded run into a throwaway store, executing nothing."""
    log = load_log(recorded.store, recorded.run_id)
    return run_script(
        script,
        run_id=recorded.run_id + "-replay",
        store=MemoryLogStore(),
        mode=ReplayMode(up_to=log.max_seq),
        log=log,
        executor=executor,
        initial=initial,
        breakers=breakers,
    )
