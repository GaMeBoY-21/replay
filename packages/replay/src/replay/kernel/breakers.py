"""Circuit breakers.

Cheap, because they live at the same gate as everything else.

The subtlety is not the tripping, it is the *rebuilding*. During replay,
`observe()` advances the same counters `check()` would have advanced, without
ever raising. Skipping that makes a resumed run start counting from zero and
walk straight back into the loop it was halted for, with the breaker unable to
see it coming.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from replay_events import Effect, Result

from .errors import BreakerTripped


class BreakerConfig(BaseModel):
    # Same (tool, arguments) shape seen this many times.
    max_repeats: int = 5
    # Cumulative model tokens.
    max_tokens: int = 100_000
    # seq ceiling — the depth of the run in effects.
    max_effects: int = 200
    # Wall clock, live only. See `check`.
    max_latency_s: float = 300.0

    def overridden(self, overrides: dict[str, Any] | None) -> "BreakerConfig":
        """A resume raises a ceiling for one attempt.

        A resume endpoint without this is a loop: replaying to the halt point and
        re-running the same step against the same ceiling trips the same breaker
        immediately.
        """
        if not overrides:
            return self
        return self.model_copy(update={k: v for k, v in overrides.items() if k in type(self).model_fields})


def extract_tokens(result: Result) -> int:
    """Best-effort token count from a recorded model result.

    Deliberately tolerant: the budget breaker is a safety rail, and a provider
    that reports usage under a name not listed here should cost an under-count,
    never an exception in the middle of a run.
    """
    value: Any = result.value
    if not isinstance(value, dict):
        return 0
    usage = value.get("usage") or value.get("Usage") or {}
    if not isinstance(usage, dict):
        return 0
    for key in ("totalTokens", "total_tokens", "total"):
        found = usage.get(key)
        if isinstance(found, int):
            return found
    parts = [usage.get(k) for k in ("inputTokens", "outputTokens", "input_tokens", "output_tokens")]
    return sum(p for p in parts if isinstance(p, int))


class Breakers:
    def __init__(self, config: BreakerConfig | None = None, now=None) -> None:
        self.config = config or BreakerConfig()
        self._counts: dict[str, int] = {}
        self._tokens = 0
        self._now = now
        self._started: float | None = None

    # ---- counters, advanced identically live and replayed ----

    def _count(self, effect: Effect) -> int:
        shape = effect.shape()
        self._counts[shape] = self._counts.get(shape, 0) + 1
        return self._counts[shape]

    def account(self, result: Result) -> None:
        """Called on every completion, live and replayed alike."""
        self._tokens += extract_tokens(result)

    # ---- the two entry points ----

    def observe(self, effect: Effect, seq: int) -> None:
        """Replay: advance the counters, never trip."""
        self._count(effect)

    def check(self, effect: Effect, seq: int) -> None:
        """Live: advance the counters, and refuse if a ceiling is crossed."""
        repeats = self._count(effect)

        if seq >= self.config.max_effects:
            raise BreakerTripped("depth", f"effect {seq} exceeds ceiling of {self.config.max_effects}")

        if effect.effect_kind == "tool" and repeats >= self.config.max_repeats:
            raise BreakerTripped(
                "loop", f"{effect.describe()} requested {repeats} times with identical arguments"
            )

        if self._tokens > self.config.max_tokens:
            raise BreakerTripped("budget", f"{self._tokens} tokens exceeds ceiling of {self.config.max_tokens}")

        # Latency is live-only and is deliberately not rebuilt during replay: the
        # recorded run's wall clock has nothing to do with the replay's, and
        # "observe" a duration would trip a replay for a delay it never had.
        if self._now is not None:
            if self._started is None:
                self._started = self._now()
            elapsed = self._now() - self._started
            if elapsed > self.config.max_latency_s:
                raise BreakerTripped("latency", f"{elapsed:.1f}s exceeds ceiling of {self.config.max_latency_s}s")
