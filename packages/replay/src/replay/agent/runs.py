"""Record, replay, fork and resume a Strands agent.

Each run is driven the same way: lineage written first, the agent built fresh
and put through the gate, the status written last. Fork and resume differ only
in the mode the context starts in and the log it is handed. Fork mode is special
exactly once, when the fork is created; replaying a fork afterwards is ordinary
replay of its resolved log.

An agent is passed as a factory rather than an instance. A run owns its agent:
the wiring rebinds the model, the executor and the state, and a second run must
not inherit the first one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from strands import Agent

from replay_events import Result, RunMetadata, RunStatus

from ..ids import new_run_id
from ..kernel import (
    BreakerConfig,
    Breakers,
    BreakerTripped,
    ForkMode,
    LiveMode,
    ReplayMode,
    RunContext,
)
from ..kernel.chain import resolve
from ..store import MemoryLogStore
from .wiring import Seam, attach

AgentFactory = Callable[[], Agent]


@dataclass
class RunOutcome:
    run_id: str
    answer: str
    agent: Agent
    seam: Seam
    store: object
    status: RunStatus

    @property
    def halted(self) -> BreakerTripped | None:
        return self.seam.hooks.halted


def _unbounded() -> Breakers:
    return Breakers(BreakerConfig(max_repeats=10**9, max_effects=10**9, max_tokens=10**9))


def _drive(store, metadata: RunMetadata, ctx: RunContext, factory: AgentFactory, prompt) -> RunOutcome:
    # Lineage FIRST, before anything the run appends. A fork whose events exist
    # but whose parent pointer never landed resolves as a root with a hole where
    # its prefix should be - an orphan, which contradicts the prefix sharing it
    # depends on. Only the status changes afterwards.
    store.put_metadata(metadata)
    try:
        agent = factory()
        seam = attach(agent, ctx)
        answer = str(agent(prompt))
    except BaseException:
        store.put_metadata(metadata.model_copy(update={"status": RunStatus.FAILED}))
        raise
    status = RunStatus.TRIPPED if seam.hooks.halted is not None else RunStatus.COMPLETED
    store.put_metadata(metadata.model_copy(update={"status": status}))
    return RunOutcome(ctx.run_id, answer, agent, seam, store, status)


def record(store, factory: AgentFactory, prompt, *, run_id: str | None = None,
           breakers: Breakers | None = None) -> RunOutcome:
    run_id = run_id or new_run_id()
    ctx = RunContext(run_id, store, LiveMode(), breakers=breakers or _unbounded())
    return _drive(store, RunMetadata(run_id=run_id), ctx, factory, prompt)


def replay(store, run_id: str, factory: AgentFactory, prompt, *, into=None) -> RunOutcome:
    """Replay a run - a fork included - into a separate store. Executes nothing."""
    log = resolve(store, run_id)
    into = into if into is not None else MemoryLogStore()
    replay_id = f"{run_id}-replay"
    ctx = RunContext(replay_id, into, ReplayMode(up_to=log.max_seq), log=log, breakers=_unbounded())
    agent = factory()
    seam = attach(agent, ctx)
    answer = str(agent(prompt))
    return RunOutcome(replay_id, answer, agent, seam, into, RunStatus.COMPLETED)


def fork(store, parent_run_id: str, at_seq: int, mutation: Result, factory: AgentFactory, prompt,
         *, run_id: str | None = None, breakers: Breakers | None = None) -> RunOutcome:
    parent = resolve(store, parent_run_id)
    if at_seq not in parent.by_seq:
        raise ValueError(f"{parent_run_id} has no effect at seq {at_seq}; its effects are 0-{parent.max_seq}")
    run_id = run_id or new_run_id()

    # The fork continues its parent's eid sequence. Restarting at zero gives the
    # resolved chain duplicate eids, invisible until something sorts by eid.
    eid_base = parent.next_eid
    metadata = RunMetadata(
        run_id=run_id,
        parent_run_id=parent_run_id,
        forked_at_seq=at_seq,
        # The replayed prefix appends nothing, so the fork's first two events are
        # the request and the substituted completion at the fork point.
        mutated_event_id=eid_base + 1,
        eid_base=eid_base,
    )
    ctx = RunContext(run_id, store, ForkMode(at=at_seq, mutation=mutation), log=parent,
                     breakers=breakers or _unbounded(), eid_base=eid_base)
    return _drive(store, metadata, ctx, factory, prompt)


def resume(store, run_id: str, factory: AgentFactory, prompt, *,
           breaker_config: BreakerConfig | None = None, breaker_overrides: dict | None = None,
           new_run_id_: str | None = None) -> RunOutcome:
    """Replay a halted run to its halt, then continue live. A fork with no mutation.

    `breaker_config` is the configuration the halted run used; it is not in the
    log, so the caller supplies it. `breaker_overrides` raises a ceiling for this
    attempt. Without one, replaying to the halt and re-running the same step
    against the same ceiling trips the same breaker again, at the same seq.
    """
    log = resolve(store, run_id)
    trips = [e for e in log.events if e.type == "BreakerTripped"]
    if not trips:
        raise ValueError(f"{run_id} did not halt; there is nothing to resume")
    halted_at = trips[-1].seq

    config = (breaker_config or BreakerConfig()).overridden(breaker_overrides)
    resumed_id = new_run_id_ or new_run_id()
    eid_base = log.next_eid
    metadata = RunMetadata(run_id=resumed_id, parent_run_id=run_id, forked_at_seq=halted_at, eid_base=eid_base)
    ctx = RunContext(resumed_id, store, ReplayMode(up_to=halted_at - 1), log=log,
                     breakers=Breakers(config), eid_base=eid_base)
    return _drive(store, metadata, ctx, factory, prompt)
