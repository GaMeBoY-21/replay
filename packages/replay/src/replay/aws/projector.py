"""Lambda `projector`: DynamoDB Streams batches in, rebuilt projections out.

The projector rebuilds each touched run from its log, so the at-least-once,
unordered delivery of Streams needs nothing more. An exception fails the batch;
the event source mapping bisects it and retries.
"""

from __future__ import annotations

from typing import Any

from ..projector.projector import project
from .resources import log_store, view_store


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    projected = project(event, log_store(), view_store())
    return {"projected": sorted(projected)}
