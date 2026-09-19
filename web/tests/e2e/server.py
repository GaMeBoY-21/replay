"""The local server, for the browser tests: the built app and the canonical runs.

    uv run python web/tests/e2e/server.py --port 4180            # can run the agent
    uv run python web/tests/e2e/server.py --port 4181 --no-model # replays only
    uv run python web/tests/e2e/server.py --port 4182 --slow 3   # each model call takes 3s

It is the real LocalApp over a fresh SQLite log seeded from fixtures/canonical,
serving web/dist. Anything that runs live after a fork point or a halt runs
against the scripted test double, so no model is called and the tests are
deterministic. --no-model is a deployment with no model at all.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tests"))

import strands_harness as H  # noqa: E402
from replay.api import Runner  # noqa: E402
from replay.kernel import BreakerConfig  # noqa: E402
from replay.local.__main__ import add_corpus, open_store  # noqa: E402
from replay.local.server import LocalApp, serve  # noqa: E402
from replay.scenario.data import TASK  # noqa: E402
from replay.scenario.live import build_agent  # noqa: E402
from replay.store.views import MemoryViewStore  # noqa: E402

ANSWER = "Reconciled. The total is $492.00 in USD."


class SlowModel(H.ScriptedModel):
    """The scripted answers, each after a delay: a live run the browser can watch.

    When slow, its first response asks for one more tool before the final answer,
    so a run cancelled during that first call has a next step to stop at."""

    def __init__(self, delay: float) -> None:
        super().__init__([H.text_response(ANSWER)])
        self.delay, self.calls = delay, 0

    async def stream(self, *args, **kwargs):
        await asyncio.sleep(self.delay)
        self.calls += 1
        one_more = self.delay and self.calls == 1
        response = (H.tool_response(H.tool_use("lookup_vendor", {"name": "Meridian Supplies"}, "call_one_more"))
                    if one_more else H.text_response(ANSWER))
        for chunk in response:
            yield chunk


def scripted_agent(delay: float = 0.0):
    return build_agent(model=SlowModel(delay))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--slow", type=float, default=0.0, help="seconds each model call takes")
    args = parser.parse_args()
    store = open_store(pathlib.Path(tempfile.mkdtemp()) / "e2e.db", REPO / "fixtures" / "canonical")
    add_corpus(store, REPO / "fixtures" / "corpus-qwen2.5-14b")
    runner = Runner(factory=lambda: scripted_agent(args.slow), prompt=TASK, breakers=BreakerConfig(max_effects=80),
                    live=not args.no_model, model=None if args.no_model else "scripted test double")
    httpd = serve(LocalApp(store, MemoryViewStore(), runner, static_dir=REPO / "web" / "dist"), port=args.port)
    print(f"e2e server on {args.port}{' (no model)' if args.no_model else ''}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
