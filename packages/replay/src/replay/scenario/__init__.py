"""The demo scenario: an invoice whose currency no record states.

The canonical runs are recorded from a real model. See docs/SCENARIO.md.
"""

from .canonical import decision_seq, load, recorded_currency_call, substitution, tool_uses
from .data import TASK

__all__ = ["TASK", "decision_seq", "load", "recorded_currency_call", "substitution", "tool_uses"]
