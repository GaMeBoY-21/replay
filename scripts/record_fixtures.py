"""Derive the frontend's fixtures from the committed canonical runs.

    uv run python scripts/record_fixtures.py

Offline and deterministic: the runs themselves are recordings, and these are
views of them. tests/test_fixtures_are_current.py requires the output to match.
"""

from __future__ import annotations

import json
import pathlib
import sys

from replay.scenario.views import fixtures
from replay.store import MemoryLogStore

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "fixtures" / "scenario"


def main() -> int:
    produced = fixtures(MemoryLogStore(), REPO / "fixtures" / "canonical")
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.json"):
        if stale.stem not in produced:
            stale.unlink()
    for name, value in produced.items():
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(f"wrote fixtures/scenario/{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
