"""Which store the suite is running against.

Every test that needs a store calls `new_store()`. The autouse `backend` fixture
in conftest decides what that returns, and parametrises every test over all
three backends - so the assertions never name a store, and the same suite passing
three times is the proof the stores are equivalent.
"""

from __future__ import annotations

import contextlib
import itertools

from replay.store import MemoryLogStore

BACKENDS = ("memory", "sqlite", "dynamodb")
PAYLOAD_BUCKET = "replay-payloads-test"

_factory = [MemoryLogStore]


def new_store():
    return _factory[0]()


def use(factory) -> None:
    _factory[0] = factory


@contextlib.contextmanager
def open_backend(name: str, tmp_path):
    """A factory for fresh, isolated stores of one backend."""
    counter = itertools.count()
    if name == "memory":
        yield MemoryLogStore
    elif name == "sqlite":
        from replay.store.sqlite import SQLiteLogStore

        yield lambda: SQLiteLogStore(tmp_path / f"store-{next(counter)}.db")
    elif name == "dynamodb":
        import boto3
        from moto import mock_aws

        from replay.store.dynamo import DynamoLogStore, create_event_table

        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=PAYLOAD_BUCKET)

            def make():
                table = f"replay-events-{next(counter)}"
                create_event_table(client, table)
                # A small page size, so every read in the suite crosses a page
                # boundary and pagination is exercised everywhere, not in one test.
                return DynamoLogStore(table, PAYLOAD_BUCKET, dynamodb=client, s3=s3, page_size=5)

            yield make
    else:
        raise ValueError(f"unknown backend {name!r}")
