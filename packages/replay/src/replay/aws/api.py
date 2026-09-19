"""Lambda `api`: API Gateway HTTP API (payload 2.0) in, the existing router out.

CloudFront forwards `/api/runs` to API Gateway unchanged; the router strips the
`/api` prefix once, as it does for the local server. Deployed without CloudFront,
API Gateway's $default route sends every other path here too, and this serves
the frontend from the copy of web/dist in the bundle - files as they are, the
app's own routes as index.html. The runner is replay-only -
live=False, no model - so /capabilities reports it and every route that would
drive the agent answers 503 with the reason, which the UI shows as unavailable.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
from typing import Any

from .. import site
from ..api import API_PREFIX, Runner, dispatch
from .resources import log_store, view_store

# bundle/replay/aws/api.py -> bundle/site, which scripts/bundle_lambda.sh fills from web/dist.
SITE = pathlib.Path(os.environ.get("SITE_DIR") or pathlib.Path(__file__).resolve().parents[2] / "site")


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


def _raw_path(event: dict[str, Any]) -> tuple[str, str]:
    http = (event.get("requestContext") or {}).get("http") or {}
    return (http.get("method") or "GET").upper(), event.get("rawPath") or http.get("path") or "/"


def _file(served: site.File, head: bool) -> dict[str, Any]:
    headers = {"content-type": served.content_type}
    if served.cache_control:
        headers["cache-control"] = served.cache_control
    body = b"" if head else served.body
    if served.is_text:
        return {"statusCode": served.status, "headers": headers, "body": body.decode(), "isBase64Encoded": False}
    return {"statusCode": served.status, "headers": headers,
            "body": base64.b64encode(body).decode(), "isBase64Encoded": True}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    method, path = _raw_path(event)
    if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
        return dispatch(_decoded(event), store=log_store(), views=view_store(), runner=RUNNER)
    if method not in ("GET", "HEAD"):
        return {"statusCode": 404, "headers": {"content-type": "application/json"},
                "body": json.dumps({"error": f"no route for {method} {path}"}), "isBase64Encoded": False}
    return _file(site.serve(SITE, path), head=method == "HEAD")
