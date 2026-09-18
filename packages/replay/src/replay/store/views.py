"""Where projections are kept. Separate from the log, and disposable.

    PK  RUN#{run_id}       SK  SUMMARY          one run's summary
    PK  TREE#{root_id}     SK  RUN#{run_id}     the fork tree of one original run
    PK  RUNS               SK  RUN#{run_id}     the list index

The list index is what lets `GET /runs` be one Query against one partition.
Enumerating runs from the event table is a Scan whose cost grows with the total
number of events across every run, not with the number of runs. Its ceiling is
worth naming: one partition bounds list throughput, and the fix - sharding the
index key - is understood and unbuilt.

Every write is an overwrite of a row derived wholesale from the log, so writing
the same projection twice is the same as writing it once.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Protocol

from replay_events import canonical


class ViewStore(Protocol):
    def put_summary(self, run_id: str, summary: dict) -> None: ...
    def get_summary(self, run_id: str) -> dict | None: ...
    def list_summaries(self) -> list[dict]: ...
    def put_tree_entry(self, root_id: str, run_id: str, entry: dict) -> None: ...
    def tree(self, root_id: str) -> list[dict]: ...


class MemoryViewStore:
    def __init__(self) -> None:
        self._summaries: dict[str, str] = {}
        self._trees: dict[str, dict[str, str]] = {}

    def put_summary(self, run_id: str, summary: dict) -> None:
        self._summaries[run_id] = canonical(summary)

    def get_summary(self, run_id: str) -> dict | None:
        body = self._summaries.get(run_id)
        return None if body is None else json.loads(body)

    def list_summaries(self) -> list[dict]:
        return [json.loads(self._summaries[r]) for r in sorted(self._summaries)]

    def put_tree_entry(self, root_id: str, run_id: str, entry: dict) -> None:
        self._trees.setdefault(root_id, {})[run_id] = canonical(entry)

    def tree(self, root_id: str) -> list[dict]:
        branch = self._trees.get(root_id, {})
        return [json.loads(branch[r]) for r in sorted(branch)]


class SQLiteViewStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS views (
        pk TEXT NOT NULL,
        sk TEXT NOT NULL,
        body TEXT NOT NULL,
        PRIMARY KEY (pk, sk)
    );
    """

    def __init__(self, path: str | os.PathLike = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(self.SCHEMA)
            self._db.commit()

    def _put(self, pk: str, sk: str, body: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO views (pk, sk, body) VALUES (?, ?, ?) "
                "ON CONFLICT(pk, sk) DO UPDATE SET body = excluded.body",
                (pk, sk, canonical(body)),
            )
            self._db.commit()

    def _query(self, pk: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM views WHERE pk = ? ORDER BY sk", (pk,)).fetchall()
        return [json.loads(body) for (body,) in rows]

    def put_summary(self, run_id: str, summary: dict) -> None:
        self._put(f"RUN#{run_id}", "SUMMARY", summary)
        self._put("RUNS", f"RUN#{run_id}", summary)

    def get_summary(self, run_id: str) -> dict | None:
        rows = self._query(f"RUN#{run_id}")
        return rows[0] if rows else None

    def list_summaries(self) -> list[dict]:
        return self._query("RUNS")

    def put_tree_entry(self, root_id: str, run_id: str, entry: dict) -> None:
        self._put(f"TREE#{root_id}", f"RUN#{run_id}", entry)

    def tree(self, root_id: str) -> list[dict]:
        return self._query(f"TREE#{root_id}")


def create_view_table(client, table_name: str) -> None:
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


class DynamoViewStore:
    """The `replay-views` table. Listing is a Query on the RUNS partition."""

    def __init__(self, table_name: str, *, dynamodb=None, region_name: str = "us-east-1") -> None:
        import boto3

        self.table_name = table_name
        self._client = dynamodb or boto3.client("dynamodb", region_name=region_name)

    def _put(self, pk: str, sk: str, body: dict) -> None:
        self._client.put_item(
            TableName=self.table_name,
            Item={"PK": {"S": pk}, "SK": {"S": sk}, "body": {"S": canonical(body)}},
        )

    def _query(self, pk: str) -> list[dict]:
        out: list[dict] = []
        request: dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk",
            "ExpressionAttributeValues": {":pk": {"S": pk}},
            "ScanIndexForward": True,
            "ConsistentRead": True,
        }
        while True:
            page = self._client.query(**request)
            out.extend(json.loads(item["body"]["S"]) for item in page["Items"])
            if not page.get("LastEvaluatedKey"):
                return out
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def put_summary(self, run_id: str, summary: dict) -> None:
        self._put(f"RUN#{run_id}", "SUMMARY", summary)
        self._put("RUNS", f"RUN#{run_id}", summary)

    def get_summary(self, run_id: str) -> dict | None:
        rows = self._query(f"RUN#{run_id}")
        return rows[0] if rows else None

    def list_summaries(self) -> list[dict]:
        return self._query("RUNS")

    def put_tree_entry(self, root_id: str, run_id: str, entry: dict) -> None:
        self._put(f"TREE#{root_id}", f"RUN#{run_id}", entry)

    def tree(self, root_id: str) -> list[dict]:
        return self._query(f"TREE#{root_id}")
