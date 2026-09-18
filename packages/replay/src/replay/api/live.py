"""The live feed - written and tested, NOT deployed or routed.

The WebSocket was cut (ARCHITECTURE.md §5). These functions are what a
`$connect` / subscribe handler and the projector's fan-out would call, kept pure
so they can be tested and wired later without being rewritten.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def subscribe(connections: dict[str, set[str]], connection_id: str, message: str) -> dict[str, Any]:
    """Register a connection's interest in one run. Returns the acknowledgement."""
    try:
        body = json.loads(message)
    except json.JSONDecodeError:
        return {"error": "the message is not valid JSON"}
    run_id = body.get("run_id") if isinstance(body, dict) else None
    if not run_id:
        return {"error": "run_id is required"}
    connections.setdefault(run_id, set()).add(connection_id)
    return {"subscribed": run_id}


def fan_out(summaries: dict[str, dict[str, Any]], connections: dict[str, set[str]],
            send: Callable[[str, dict[str, Any]], bool]) -> dict[str, int]:
    """Push each rebuilt summary to the connections watching that run.

    `send` returns False for a connection that has gone; it is dropped, so a
    dead socket costs one failed send rather than one per batch forever.
    """
    delivered = 0
    for run_id, summary in summaries.items():
        for connection_id in sorted(connections.get(run_id, ())):
            if send(connection_id, {"run_id": run_id, "summary": summary}):
                delivered += 1
            else:
                connections[run_id].discard(connection_id)
    return {"delivered": delivered}
