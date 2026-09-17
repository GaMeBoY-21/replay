"""The committed scenario fixtures are what the code produces now.

The frontend is built against these files without a model or an API. A fixture
that drifted from the code is a UI built against a run that no longer exists -
so they are regenerated here and compared byte for byte. This runs against every
backend, so it also proves all three stores record the canonical runs
identically. Regenerate with `uv run python scripts/record_fixtures.py`.
"""

from __future__ import annotations

import json
import pathlib

from backends import new_store
from replay.scenario import fixtures, record_canonical

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "scenario"


def render(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def test_the_committed_fixtures_are_current():
    store = new_store()
    record_canonical(store)
    produced = fixtures(store)

    committed = {path.stem for path in FIXTURES.glob("*.json")}
    assert committed == set(produced), f"fixture files differ from what is produced: {committed ^ set(produced)}"
    for name, value in produced.items():
        assert (FIXTURES / f"{name}.json").read_text() == render(value), (
            f"fixtures/scenario/{name}.json is stale - run scripts/record_fixtures.py"
        )
