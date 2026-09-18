"""API Gateway HTTP API (payload 2.0) in and out, and nothing else."""

from __future__ import annotations

import json
from typing import Any

JSON = {"content-type": "application/json"}


def response(status: int, body: Any) -> dict[str, Any]:
    return {"statusCode": status, "headers": dict(JSON), "body": json.dumps(body, default=str)}


def ok(body: Any) -> dict[str, Any]:
    return response(200, body)


def created(body: Any) -> dict[str, Any]:
    return response(201, body)


def bad_request(message: str, **extra: Any) -> dict[str, Any]:
    return response(400, {"error": message, **extra})


def not_found(message: str) -> dict[str, Any]:
    return response(404, {"error": message})


def conflict(message: str) -> dict[str, Any]:
    return response(409, {"error": message})


def body_of(event: dict[str, Any]) -> dict[str, Any]:
    """The JSON body, or {}. A body that is not a JSON object is a client error."""
    raw = event.get("body")
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError(f"the body is not valid JSON: {exc.msg}") from None
    if not isinstance(parsed, dict):
        raise ValueError("the body must be a JSON object")
    return parsed


def query_of(event: dict[str, Any]) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def path_param(event: dict[str, Any], name: str) -> str:
    return (event.get("pathParameters") or {})[name]


def int_param(params: dict[str, str], name: str, default: int | None = None) -> int | None:
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
