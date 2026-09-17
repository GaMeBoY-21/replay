"""The determinism property test.

The single most important test in the project, which is why it is written on day
one alongside the kernel rather than after it. Hypothesis generates runs and
shrinks any failure to a minimal counterexample, which turns "replay drifted
somewhere in twenty steps" into "replay drifts when a tool returns an empty
dict". That difference is worth hours.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness import Step, poisoned_executor, record, replay, unbounded
from replay_events import canonical

KEYS = ["alpha", "beta", "gamma", "delta"]

json_scalars = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.text(max_size=12),
    st.booleans(),
    st.none(),
)

arguments = st.dictionaries(
    st.sampled_from(["q", "id", "limit", "mode", "system"]),
    json_scalars,
    max_size=4,
)

steps = st.builds(
    Step,
    kind=st.sampled_from(["tool", "model", "clock", "random"]),
    name=st.sampled_from(["search", "fetch", "score", "summarise", "now", "uuid4"]),
    args=arguments,
    reads=st.lists(st.sampled_from(KEYS), max_size=3).map(tuple),
    writes=st.one_of(st.none(), st.sampled_from(KEYS)),
)

scripts = st.lists(steps, min_size=1, max_size=20)


@given(script=scripts)
# function_scoped_fixture: the autouse `backend` fixture only selects a store
# factory; every example builds its own fresh stores, so nothing leaks between them.
@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_replay_is_byte_identical(script):
    """CLAIM: for any recorded run, replaying it reproduces identical state."""
    recorded = record(script, breakers=unbounded())
    replayed = replay(recorded, script, executor=poisoned_executor, breakers=unbounded())
    assert canonical(replayed.state) == canonical(recorded.state)
    assert canonical(replayed.served) == canonical(recorded.served)


@given(script=scripts)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_eids_are_unique_and_ordered(script):
    """CLAIM: eid is monotonic over every event and is the sole ordering key.

    Not just over effects. Memory reads and writes, step boundaries and breaker
    trips all take their own eid and interleave — which is the property that
    fails the moment anything conflates eid with seq.
    """
    recorded = record(script, breakers=unbounded())
    events = recorded.store.read(recorded.run_id)
    eids = [e.eid for e in events]
    assert eids == sorted(eids)
    assert len(eids) == len(set(eids))
    assert eids == list(range(len(eids)))


@given(script=scripts)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_one_effect_appends_two_events_at_one_seq(script):
    """CLAIM: seq is the effect index, and one effect closes exactly once."""
    recorded = record(script, breakers=unbounded())
    events = recorded.store.read(recorded.run_id)
    requested = [e.seq for e in events if e.type == "EffectRequested"]
    completed = [e.seq for e in events if e.type == "EffectCompleted"]
    assert requested == list(range(len(script)))
    assert completed == requested
