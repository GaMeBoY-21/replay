"""Circuit breakers, and the counter-rebuilding that makes resume work."""

from __future__ import annotations

import pytest

from replay.kernel import BreakerConfig, Breakers, BreakerTripped
from replay.kernel.breakers import extract_tokens
from replay_events import ModelEffect, Result, ToolEffect


def tool(n: int = 1) -> ToolEffect:
    return ToolEffect(name="lookup", arguments={"id": n})


def test_the_loop_breaker_trips_on_the_fifth_identical_call():
    """CLAIM: same (tool, arguments) seen five times."""
    breakers = Breakers(BreakerConfig(max_repeats=5))
    for seq in range(4):
        breakers.check(tool(), seq)
    with pytest.raises(BreakerTripped) as trip:
        breakers.check(tool(), 4)
    assert trip.value.name == "loop"


def test_the_loop_breaker_does_not_trip_on_different_arguments():
    breakers = Breakers(BreakerConfig(max_repeats=3))
    for seq in range(10):
        breakers.check(tool(seq), seq)


def test_the_depth_breaker_trips_on_the_seq_ceiling():
    breakers = Breakers(BreakerConfig(max_effects=3))
    for seq in range(3):
        breakers.check(tool(seq), seq)
    with pytest.raises(BreakerTripped) as trip:
        breakers.check(tool(99), 3)
    assert trip.value.name == "depth"


def test_the_budget_breaker_trips_on_accumulated_tokens():
    breakers = Breakers(BreakerConfig(max_tokens=100))
    breakers.check(ModelEffect(messages=[{"role": "user", "content": "a"}]), 0)
    breakers.account(Result(value={"usage": {"totalTokens": 250}}))
    with pytest.raises(BreakerTripped) as trip:
        breakers.check(ModelEffect(messages=[{"role": "user", "content": "b"}]), 1)
    assert trip.value.name == "budget"


def test_the_latency_breaker_uses_an_injected_clock():
    """Wall clock is injected, never read from the module.

    A breaker that called `time.time()` itself would be one more un-gated source
    of non-determinism in the one file that exists to have none.
    """
    ticks = iter([0.0, 1.0, 999.0])
    breakers = Breakers(BreakerConfig(max_latency_s=10.0), now=lambda: next(ticks))
    breakers.check(tool(1), 0)
    with pytest.raises(BreakerTripped) as trip:
        breakers.check(tool(2), 1)
    assert trip.value.name == "latency"


def test_observe_never_trips():
    """CLAIM: breakers observe during replay but never trip.

    A replay that tripped would be unable to reproduce the very run that was
    halted — which is the run anyone most wants to look at.
    """
    breakers = Breakers(BreakerConfig(max_repeats=2, max_effects=1))
    for seq in range(20):
        breakers.observe(tool(), seq)


def test_observe_rebuilds_the_counters_it_does_not_trip_on():
    """CLAIM: counters are rebuilt, not reset.

    This is the whole reason resume works. A resumed run that started counting
    from zero would walk straight back into the loop it was halted for, with the
    breaker unable to see it — the failure mode being an endpoint that looks
    like a fix and is a loop.
    """
    breakers = Breakers(BreakerConfig(max_repeats=5))
    for seq in range(4):
        breakers.observe(tool(), seq)  # the replayed prefix
    with pytest.raises(BreakerTripped) as trip:
        breakers.check(tool(), 4)  # the first live step after the prefix
    assert trip.value.name == "loop"


def test_an_override_raises_a_ceiling_for_one_attempt():
    """CLAIM: a resume without an override is a loop."""
    config = BreakerConfig(max_repeats=5)
    raised = config.overridden({"max_repeats": 50})
    breakers = Breakers(raised)
    for seq in range(20):
        breakers.check(tool(), seq)
    assert config.max_repeats == 5, "the override must not mutate the original"


def test_an_override_ignores_unknown_keys():
    config = BreakerConfig()
    assert config.overridden({"not_a_breaker": 1}) == config


def test_token_extraction_is_tolerant():
    assert extract_tokens(Result(value=None)) == 0
    assert extract_tokens(Result(value={"usage": "nonsense"})) == 0
    assert extract_tokens(Result(value={"usage": {"totalTokens": 7}})) == 7
    assert extract_tokens(Result(value={"usage": {"inputTokens": 3, "outputTokens": 4}})) == 7
