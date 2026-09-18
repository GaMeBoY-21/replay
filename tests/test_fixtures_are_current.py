"""The committed frontend fixtures are what the canonical runs produce now.

The frontend is built against these files without a model or an API. They are
views of committed recordings, so they are regenerated here - against every
store - and compared byte for byte. Regenerate with scripts/record_fixtures.py.
"""

from __future__ import annotations

import json
import pathlib

from backends import new_store
from replay.scenario.views import fixtures

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures" / "scenario"


def render(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def test_the_committed_fixtures_are_current():
    produced = fixtures(new_store(), REPO / "fixtures" / "canonical")
    committed = {path.stem for path in FIXTURES.glob("*.json")}
    assert committed == set(produced), f"fixture files differ: {committed ^ set(produced)}"
    for name, value in produced.items():
        assert (FIXTURES / f"{name}.json").read_text() == render(value), (
            f"fixtures/scenario/{name}.json is stale - run scripts/record_fixtures.py"
        )
