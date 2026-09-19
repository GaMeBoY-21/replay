"""The deployed stores, built once per Lambda container from its environment.

    EVENTS_TABLE    the log (PK RUN#id, SK EVT#eid | METADATA), Streams on
    VIEWS_TABLE     the projections (RUNS index, SUMMARY, TREE#root)
    PAYLOAD_BUCKET  events whose body exceeds 100 KB
    AWS_REGION      set by Lambda itself

The stores default to us-east-1 when no region is given, so the region is always
passed explicitly: a store pointed at the wrong region finds no tables at all.
"""

from __future__ import annotations

import os
from functools import lru_cache

from ..store.dynamo import DynamoLogStore
from ..store.views import DynamoViewStore


def region() -> str:
    return os.environ.get("AWS_REGION") or os.environ["AWS_DEFAULT_REGION"]


@lru_cache(maxsize=1)
def log_store() -> DynamoLogStore:
    return DynamoLogStore(os.environ["EVENTS_TABLE"], os.environ["PAYLOAD_BUCKET"], region_name=region())


@lru_cache(maxsize=1)
def view_store() -> DynamoViewStore:
    return DynamoViewStore(os.environ["VIEWS_TABLE"], region_name=region())
