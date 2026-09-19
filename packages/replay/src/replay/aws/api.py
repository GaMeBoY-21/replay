"""Lambda `api`: API Gateway HTTP API (payload 2.0) in, the existing router out.

CloudFront forwards `/api/runs` to API Gateway unchanged; the router strips the
`/api` prefix once, as it does for the local server. The runner is replay-only -
live=False, no model - so /capabilities reports it and every route that would
drive the agent answers 503 with the reason, which the UI shows as unavailable.
"""

from __future__ import annotations

import base64
from typing import Any

from ..api import Runner, dispatch
from .resources import log_store, view_store


def _no_agent():
    raise RuntimeError("this deployment replays recordings and has no model to run an agent with")


# The same as `python -m replay.local --replay-only`, without importing the local
# entry point, which imports the live agent.
RUNNER = Runner(factory=_no_agent, prompt="", live=False, model=None)


def _decoded(event: dict[str, Any]) -> dict[str, Any]:
    """API Gateway may deliver a body base64-encoded; the router reads text."""
    if event.get("isBase64Encoded") and event.get("body"):
        return {**event, "body": base64.b64decode(event["body"]).decode(), "isBase64Encoded": False}
    return event


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    return dispatch(_decoded(event), store=log_store(), views=view_store(), runner=RUNNER)
