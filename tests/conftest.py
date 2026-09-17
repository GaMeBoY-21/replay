"""Collection-time guards.

The verify-in-flight guard is not a convenience. `scripts/verify_claims.sh`
deliberately mutates source files in place; a pytest run that starts while it is
in flight imports broken source. One such overlap previously drove two Python
processes to ~35 GB of footprint each on a 24 GB machine, because a mutated
fork-chain resolver turned a 2000-deep chain into unbounded recursion.

Refusing collection is the only reliable stop: by the time a test body runs, the
mutated module is already imported.
"""

from __future__ import annotations

import os
import pathlib

import pytest

import backends
from replay.store import MemoryLogStore

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INFLIGHT = REPO_ROOT / ".verify-claims-inflight"

# The harness itself has to run pytest against mutated source — that is the
# whole point of it. It opts in explicitly; nothing else does.
HARNESS_OPT_IN = os.environ.get("REPLAY_VERIFY") == "1"


def pytest_collection(session: pytest.Session) -> None:
    if INFLIGHT.exists() and not HARNESS_OPT_IN:
        raise pytest.UsageError(
            f"{INFLIGHT.name} exists: `make verify` is mutating source right now. "
            "Running pytest against deliberately-broken source has previously "
            "exhausted this machine's memory. Wait for verify to finish, or "
            "remove the marker if you are certain it is stale."
        )


# ---------------------------------------------------------------- the backends
#
# Every test runs against all three stores. The assertions never name one: a test
# asks `backends.new_store()` for a store, and this fixture decides which kind it
# gets. The identical suite passing three times is the equivalence proof.


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "single_backend: runs once, because it constructs the stores it compares itself",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "backend" in metafunc.fixturenames and not metafunc.definition.get_closest_marker("single_backend"):
        metafunc.parametrize("backend", backends.BACKENDS, indirect=True)


@pytest.fixture(autouse=True)
def backend(request: pytest.FixtureRequest, tmp_path):
    name = getattr(request, "param", "memory")
    with backends.open_backend(name, tmp_path) as factory:
        backends.use(factory)
        try:
            yield name
        finally:
            backends.use(MemoryLogStore)
