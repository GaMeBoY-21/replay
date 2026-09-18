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

class Factory:
    """Fresh, isolated log stores of one backend, and view stores beside them."""

    def __init__(self, logs, views):
        self._logs, self.views = logs, views

    def __call__(self):
        return self._logs()


def _memory_views():
    from replay.store.views import MemoryViewStore

    return MemoryViewStore()


_factory = [Factory(MemoryLogStore, _memory_views)]


def new_store():
    return _factory[0]()


def new_views():
    return _factory[0].views()


def use(factory) -> None:
    if not isinstance(factory, Factory):
        factory = Factory(factory, _memory_views)
    _factory[0] = factory


@contextlib.contextmanager
def open_backend(name: str, tmp_path):
    """A factory for fresh, isolated stores of one backend."""
    counter = itertools.count()
    if name == "memory":
        yield Factory(MemoryLogStore, _memory_views)
    elif name == "sqlite":
        from replay.store.sqlite import SQLiteLogStore
        from replay.store.views import SQLiteViewStore

        yield Factory(lambda: SQLiteLogStore(tmp_path / f"store-{next(counter)}.db"),
                      lambda: SQLiteViewStore(tmp_path / f"views-{next(counter)}.db"))
    elif name == "dynamodb":
        import boto3
        from moto import mock_aws

        from replay.store.dynamo import DynamoLogStore, create_event_table
        from replay.store.views import DynamoViewStore, create_view_table

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

            def make_views():
                table = f"replay-views-{next(counter)}"
                create_view_table(client, table)
                return DynamoViewStore(table, dynamodb=client)

            yield Factory(make, make_views)
    else:
        raise ValueError(f"unknown backend {name!r}")
