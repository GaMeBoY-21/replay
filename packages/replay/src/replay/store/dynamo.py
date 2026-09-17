"""The DynamoDB log store.

    PK  RUN#{run_id}    SK  EVT#{eid:09d}    one event
    PK  RUN#{run_id}    SK  METADATA         lineage and status

The sort key is zero-padded, so lexicographic order is numeric order and a Query
with ScanIndexForward=True returns the run in order with no client-side sort.

Append is conditional on `attribute_not_exists(SK)`. Two writers can never claim
the same eid, which makes append-only a guarantee rather than a convention.

An event whose body exceeds 100KB is written to S3 and the item carries
`payload_ref` instead of `payload` - never both. Items cap at 400KB, and a
recorded model result is a chunk list, the payload most likely to cross it. The
object is written BEFORE the item, so a reference can never point at an object
that does not exist; a failed conditional put leaves an unreferenced object,
which is litter, not corruption.

The low-level client is used rather than the resource API: the resource
deserialises numbers as `Decimal`. The body is stored as canonical JSON text, so
no number in a recorded result ever passes through DynamoDB's number type.
"""

from __future__ import annotations

from replay_events import Event, RunMetadata, RunNotFound

from .codec import decode_event, decode_metadata, encode_event, encode_metadata
from .protocol import EventIdConflict

INLINE_LIMIT = 100 * 1024
METADATA_SK = "METADATA"


def create_event_table(client, table_name: str) -> None:
    """The table this store expects. Used by tests; the CDK stack declares the same keys."""
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _pk(run_id: str) -> str:
    return f"RUN#{run_id}"


def _sk(eid: int) -> str:
    return f"EVT#{eid:09d}"


class DynamoLogStore:
    def __init__(self, table_name: str, bucket: str | None = None, *, dynamodb=None, s3=None,
                 region_name: str = "us-east-1", page_size: int | None = None) -> None:
        import boto3

        self.table_name = table_name
        self.bucket = bucket
        self._client = dynamodb or boto3.client("dynamodb", region_name=region_name)
        self._s3 = s3 if s3 is not None else (boto3.client("s3", region_name=region_name) if bucket else None)
        self._page_size = page_size

    # ---- events ----

    def append(self, run_id: str, event: Event) -> None:
        body = encode_event(event)
        item = {
            "PK": {"S": _pk(run_id)},
            "SK": {"S": _sk(event.eid)},
            "type": {"S": event.type},
            "eid": {"N": str(event.eid)},
        }
        seq = getattr(event, "seq", None)
        if seq is not None:
            item["seq"] = {"N": str(seq)}

        if len(body.encode()) > INLINE_LIMIT:
            if self._s3 is None or self.bucket is None:
                raise ValueError(f"{run_id}: eid {event.eid} is {len(body.encode())} bytes and no bucket is configured")
            key = f"events/{run_id}/{event.eid:09d}.json"
            self._s3.put_object(Bucket=self.bucket, Key=key, Body=body.encode())
            item["payload_ref"] = {"S": f"s3://{self.bucket}/{key}"}
        else:
            item["payload"] = {"S": body}

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(SK)",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            raise EventIdConflict(f"{run_id}: eid {event.eid} is already taken") from None

    def read(self, run_id: str) -> list[Event]:
        events: list[Event] = []
        request = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :evt)",
            "ExpressionAttributeValues": {":pk": {"S": _pk(run_id)}, ":evt": {"S": "EVT#"}},
            "ScanIndexForward": True,
            "ConsistentRead": True,
        }
        if self._page_size:
            request["Limit"] = self._page_size
        while True:
            page = self._client.query(**request)
            events.extend(decode_event(self._body(item)) for item in page["Items"])
            last = page.get("LastEvaluatedKey")
            if not last:
                return events
            request["ExclusiveStartKey"] = last

    def _body(self, item: dict) -> str:
        if "payload" in item:
            return item["payload"]["S"]
        ref = item["payload_ref"]["S"]
        bucket, key = ref.removeprefix("s3://").split("/", 1)
        return self._s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()

    # ---- metadata ----

    def put_metadata(self, metadata: RunMetadata) -> None:
        self._client.put_item(
            TableName=self.table_name,
            Item={
                "PK": {"S": _pk(metadata.run_id)},
                "SK": {"S": METADATA_SK},
                "run_id": {"S": metadata.run_id},
                "body": {"S": encode_metadata(metadata)},
            },
        )

    def get_metadata(self, run_id: str) -> RunMetadata:
        item = self._client.get_item(
            TableName=self.table_name,
            Key={"PK": {"S": _pk(run_id)}, "SK": {"S": METADATA_SK}},
            ConsistentRead=True,
        ).get("Item")
        if item is None:
            raise RunNotFound(run_id)
        return decode_metadata(item["body"]["S"])

    def list_runs(self) -> list[RunMetadata]:
        # A Scan. §13 replaces this with a list index in the read model; the log
        # store answers it the slow way because it is the source of truth.
        runs: list[RunMetadata] = []
        request = {
            "TableName": self.table_name,
            "FilterExpression": "SK = :m",
            "ExpressionAttributeValues": {":m": {"S": METADATA_SK}},
            "ConsistentRead": True,
        }
        while True:
            page = self._client.scan(**request)
            runs.extend(decode_metadata(item["body"]["S"]) for item in page["Items"])
            last = page.get("LastEvaluatedKey")
            if not last:
                return sorted(runs, key=lambda m: m.run_id)
            request["ExclusiveStartKey"] = last
