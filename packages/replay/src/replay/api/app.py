"""The router. Routing and error mapping, and no decisions of its own.

The `/api` prefix is stripped here, once. The frontend and the API share one
origin - the app under `/`, the API under `/api/*` - and CloudFront forwards the
full path to API Gateway, prefix included. Stripping it in each handler means one
handler that forgot. Handlers never see a path at all: they get path parameters.
"""

from __future__ import annotations

import re
from typing import Any

from replay_events import RunNotFound

from ..kernel.errors import UnresolvableRun
from . import handlers
from .http import bad_request, not_found

API_PREFIX = "/api"

ROUTES: list[tuple[str, re.Pattern, Any]] = [
    ("POST", re.compile(r"^/runs$"), handlers.start_run),
    ("GET", re.compile(r"^/runs$"), handlers.list_runs),
    ("GET", re.compile(r"^/runs/(?P<id>[^/]+)$"), handlers.get_run),
    ("GET", re.compile(r"^/runs/(?P<id>[^/]+)/events$"), handlers.get_events),
    ("POST", re.compile(r"^/runs/(?P<id>[^/]+)/fork$"), handlers.fork_run),
    ("POST", re.compile(r"^/runs/(?P<id>[^/]+)/resume$"), handlers.resume_run),
    ("POST", re.compile(r"^/runs/(?P<id>[^/]+)/cancel$"), handlers.cancel_run),
    ("GET", re.compile(r"^/runs/(?P<id>[^/]+)/trace/output$"), handlers.trace_output),
    ("GET", re.compile(r"^/runs/(?P<id>[^/]+)/trace$"), handlers.trace_run),
    ("GET", re.compile(r"^/diff$"), handlers.diff),
    ("GET", re.compile(r"^/capabilities$"), handlers.capabilities),
]


def method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or http.get("path") or event.get("path") or "/"
    if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
        path = path[len(API_PREFIX):] or "/"
    return method, path.rstrip("/") or "/"


def dispatch(event: dict[str, Any], **deps: Any) -> dict[str, Any]:
    method, path = method_and_path(event)
    for verb, pattern, handler in ROUTES:
        match = pattern.match(path)
        if match and verb == method:
            routed = {**event, "pathParameters": match.groupdict()}
            try:
                return handler(routed, **deps)
            except ValueError as exc:
                return bad_request(str(exc))
            except (RunNotFound, UnresolvableRun) as exc:
                return not_found(f"no such run: {exc}")
    return not_found(f"no route for {method} {path}")


def http_event(method: str, path: str, *, query: dict | None = None, body: Any = None) -> dict[str, Any]:
    """An API Gateway HTTP API 2.0 event, as the deployed Lambda receives one."""
    import json

    return {
        "version": "2.0",
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": {k: str(v) for k, v in (query or {}).items()} or None,
        "body": None if body is None else (body if isinstance(body, str) else json.dumps(body)),
    }
