from __future__ import annotations


class ReplayError(Exception):
    """Base for every kernel failure."""


class DivergenceError(ReplayError):
    """A replayed step asked for something different from what was recorded.

    This is the only detector for an entire class of silent drift — an un-gated
    clock read, an un-gated random draw, a concurrently-dispatched tool call.
    It must fail loudly and immediately. A replay that carries on past a
    divergence produces a plausible wrong answer, which is strictly worse than
    an exception.
    """


class UnresolvableRun(ReplayError):
    """A run in a fork chain has no metadata.

    Without it there is no parent pointer, and treating the run as a root would
    return a shorter, wrong, entirely plausible log with its prefix missing.
    """


class ReplayExhausted(ReplayError):
    """Replay asked for an effect the log does not contain."""


class BreakerTripped(ReplayError):
    """A breaker refused to let an effect run.

    The seq is already claimed when this is raised, so a resume replays to here
    and retries the same step — against an overridden ceiling, or it trips again
    immediately.
    """

    def __init__(self, name: str, detail: str = "") -> None:
        super().__init__(f"{name}: {detail}" if detail else name)
        self.name = name
        self.detail = detail
