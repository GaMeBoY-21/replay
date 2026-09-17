"""Record the canonical scenario runs and write them as fixtures.

    uv run python scripts/record_fixtures.py

Deterministic: a scripted provider and a counting clock, so the output is the
same every time and tests/test_fixtures_are_current.py can require it.
"""

from __future__ import annotations

import json
import pathlib
import sys

from replay.scenario import fixtures, record_canonical
from replay.store import MemoryLogStore

OUT = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "scenario"


def main() -> int:
    store = MemoryLogStore()
    record_canonical(store)
    OUT.mkdir(parents=True, exist_ok=True)
    produced = fixtures(store)
    for stale in OUT.glob("*.json"):
        if stale.stem not in produced:
            stale.unlink()
    for name, value in produced.items():
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        print(f"wrote fixtures/scenario/{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
