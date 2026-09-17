"""The demo scenario: an invoice reconciliation that is wrong from step 3. See docs/SCENARIO.md."""

from .canonical import (
    FORK_A,
    FORK_STEP,
    LOOP_CEILING,
    RUN_A,
    RUN_B,
    SUBSTITUTED,
    build_agent,
    counting_clock,
    fixtures,
    fork_run_b,
    record_canonical,
    record_run_a,
    record_run_b,
    resume_run_a,
    substitution,
)
from .data import TASK
from .model import ScenarioModel

__all__ = [
    "FORK_A", "FORK_STEP", "LOOP_CEILING", "RUN_A", "RUN_B", "SUBSTITUTED", "TASK", "ScenarioModel",
    "build_agent", "counting_clock", "fixtures", "fork_run_b", "record_canonical", "record_run_a",
    "record_run_b", "resume_run_a", "substitution",
]
