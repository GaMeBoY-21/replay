"""The demo scenario: an invoice whose currency no record states.

The canonical runs are recorded from a real model. See docs/SCENARIO.md.
"""

from .canonical import SUBSTITUTED, conversion_seq, load, substitution
from .data import TASK

__all__ = ["SUBSTITUTED", "TASK", "conversion_seq", "load", "substitution"]
