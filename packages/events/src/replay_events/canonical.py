"""Canonical JSON.

Two places depend on a stable byte-for-byte rendering of arbitrary JSON data:
the divergence detector, which compares a recorded effect against a live one,
and anything that hashes a payload.

`dict` preserves insertion order, and insertion order is not stable across a
record and a replay — the same logical arguments can arrive with their keys in a
different order and compare unequal while being identical. Sorting keys is a
determinism rule, not a nicety.
"""

from __future__ import annotations

import json
from typing import Any


def canonical(value: Any) -> str:
    """A stable string rendering of JSON-shaped data.

    `sort_keys` fixes key order at every depth. `separators` removes the
    whitespace variance that would otherwise depend on the encoder's defaults.
    `default=str` keeps this total: a value that is not JSON-native still
    renders rather than raising inside a comparison.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
