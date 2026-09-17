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
