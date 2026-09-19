"""The local server, for the browser tests: the built app and the canonical runs.

    uv run python web/tests/e2e/server.py --port 4180            # can run the agent
    uv run python web/tests/e2e/server.py --port 4181 --no-model # replays only

It is the real LocalApp over a fresh SQLite log seeded from fixtures/canonical,
serving web/dist. Anything that runs live after a fork point or a halt runs
against the scripted test double, so no model is called and the tests are
deterministic. --no-model is a deployment with no model at all.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests"))

import strands_harness as H  # noqa: E402
from replay.api import Runner  # noqa: E402
from replay.kernel import BreakerConfig  # noqa: E402
from replay.local.__main__ import open_store  # noqa: E402
from replay.local.server import LocalApp, serve  # noqa: E402
from replay.scenario.data import TASK  # noqa: E402
from replay.scenario.live import build_agent  # noqa: E402
from replay.store.views import MemoryViewStore  # noqa: E402

ANSWER = "Reconciled. The total is $492.00 in USD."


def scripted_agent():
    return build_agent(model=H.ScriptedModel([H.text_response(ANSWER) for _ in range(4)]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--no-model", action="store_true")
    args = parser.parse_args()
    store = open_store(pathlib.Path(tempfile.mkdtemp()) / "e2e.db", REPO / "fixtures" / "canonical")
    runner = Runner(factory=scripted_agent, prompt=TASK, breakers=BreakerConfig(max_effects=80),
                    live=not args.no_model, model=None if args.no_model else "scripted test double")
    httpd = serve(LocalApp(store, MemoryViewStore(), runner, static_dir=REPO / "web" / "dist"), port=args.port)
    print(f"e2e server on {args.port}{' (no model)' if args.no_model else ''}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
