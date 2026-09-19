"""The Lambda entry points, against moto: what API Gateway and Streams deliver.

The deployed product replays recordings only. These call the real `api` and
`projector` handlers with the events AWS sends - an HTTP API 2.0 event whose path
still carries the `/api` prefix CloudFront forwards, and a Streams batch - over
DynamoDB and S3 as the stack declares them.
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.single_backend

REPO = pathlib.Path(__file__).resolve().parent.parent
CANONICAL = REPO / "fixtures" / "canonical"
MANIFEST = json.loads((CANONICAL / "manifest.json").read_text())
REGION = "ap-south-1"


def gateway_event(method: str, path: str, query: dict | None = None, body=None, base64_body=False) -> dict:
    """An API Gateway HTTP API payload 2.0 event, as the api Lambda receives it."""
    text = None if body is None else json.dumps(body)
    return {
        "version": "2.0",
        "routeKey": "ANY /api/{proxy+}",
        "rawPath": path,
        "rawQueryString": "&".join(f"{k}={v}" for k, v in (query or {}).items()),
        "queryStringParameters": query,
        "headers": {"content-type": "application/json", "host": "example.cloudfront.net"},
        "requestContext": {"http": {"method": method, "path": path, "protocol": "HTTP/1.1"}, "stage": "$default"},
        "body": base64.b64encode(text.encode()).decode() if (text and base64_body) else text,
        "isBase64Encoded": bool(text and base64_body),
    }


@pytest.fixture
def deployed(monkeypatch):
    import boto3
    from moto import mock_aws

    with mock_aws():
        monkeypatch.setenv("AWS_REGION", REGION)
        monkeypatch.setenv("EVENTS_TABLE", "replay-events")
        monkeypatch.setenv("VIEWS_TABLE", "replay-views")
        monkeypatch.setenv("PAYLOAD_BUCKET", "replay-payloads")
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            monkeypatch.setenv(name, "testing")
        from replay.aws import resources
        from replay.scenario import load
        from replay.store.dynamo import create_event_table
        from replay.store.views import create_view_table

        client = boto3.client("dynamodb", region_name=REGION)
        create_event_table(client, "replay-events")
        create_view_table(client, "replay-views")
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="replay-payloads", CreateBucketConfiguration={"LocationConstraint": REGION})
        resources.log_store.cache_clear()
        resources.view_store.cache_clear()
        load(resources.log_store(), CANONICAL)
        yield resources
        resources.log_store.cache_clear()
        resources.view_store.cache_clear()


def call(method, path, query=None, body=None, base64_body=False):
    from replay.aws.api import handler

    response = handler(gateway_event(method, path, query, body, base64_body))
    return response["statusCode"], json.loads(response["body"])


def project_everything(resources):
    """What the Streams projector sees after the seed: a batch touching every run."""
    from replay.aws.projector import handler

    records = [{"eventName": "INSERT", "dynamodb": {"Keys": {"PK": {"S": f"RUN#{m.run_id}"}, "SK": {"S": "METADATA"}}}}
               for m in resources.log_store().list_runs()]
    return handler({"Records": records})


def test_the_stores_are_built_in_the_lambda_region_not_the_default(deployed):
    assert deployed.log_store()._client.meta.region_name == REGION
    assert deployed.view_store()._client.meta.region_name == REGION


def test_the_projector_entry_rebuilds_every_run_a_batch_touched(deployed):
    projected = project_everything(deployed)
    assert set(projected["projected"]) == {m.run_id for m in deployed.log_store().list_runs()}
    assert deployed.view_store().get_summary(MANIFEST["wrong"]["run_id"])["status"] == "completed"


def test_the_prefix_cloudfront_forwards_is_stripped_at_the_router(deployed):
    project_everything(deployed)
    status, body = call("GET", "/api/runs")
    assert status == 200 and len(body["runs"]) == 7
    assert call("GET", f"/api/runs/{MANIFEST['wrong']['run_id']}")[0] == 200
    status, body = call("GET", "/api/nowhere")
    assert status == 404 and "no route" in body["error"]


def test_the_deployment_reads_traces_and_diffs(deployed):
    wrong, fork = MANIFEST["wrong"]["run_id"], MANIFEST["fork"]["run_id"]
    status, trace = call("GET", f"/api/runs/{wrong}/trace/output")
    assert status == 200 and (trace["head"]["key"], trace["head"]["value"]) == ("invoice.currency", "USD")
    status, diff = call("GET", "/api/diff", {"a": wrong, "b": fork})
    assert status == 200 and diff["shared_by"] == "storage"


def test_the_deployment_replays_only_and_says_so(deployed):
    status, caps = call("GET", "/api/capabilities")
    assert (status, caps["live"], caps["model"]) == (200, False, None)
    wrong, halted = MANIFEST["wrong"]["run_id"], MANIFEST["halted"]["run_id"]
    mutation = {"toolUseId": "t", "status": "success", "content": [{"text": "{}"}]}
    status, body = call("POST", f"/api/runs/{wrong}/fork", body={"at_step": 9, "mutation": mutation}, base64_body=True)
    assert status == 503 and "replays recordings" in body["error"], "a fork is refused with the reason, not a 500"
    status, body = call("POST", f"/api/runs/{halted}/resume", body={"breaker_overrides": {"max_effects": 80}})
    assert status == 503


def test_the_api_entry_never_loads_strands():
    """Replay-only must not import the agent: run the entry with `strands` blocked."""
    script = textwrap.dedent(f"""
        import builtins, json, os, sys
        real_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == "strands" or name.startswith("strands."):
                raise ImportError("strands is blocked in this test")
            return real_import(name, *args, **kwargs)
        builtins.__import__ = guarded

        import boto3
        from moto import mock_aws
        os.environ.update(AWS_REGION="{REGION}", EVENTS_TABLE="e", VIEWS_TABLE="v", PAYLOAD_BUCKET="p",
                          AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")
        with mock_aws():
            from replay.store.dynamo import create_event_table
            from replay.store.views import create_view_table
            client = boto3.client("dynamodb", region_name="{REGION}")
            create_event_table(client, "e")
            create_view_table(client, "v")
            from replay.aws.api import handler
            response = handler({{"version": "2.0", "rawPath": "/api/runs",
                                 "requestContext": {{"http": {{"method": "GET", "path": "/api/runs"}}}}}})
            assert response["statusCode"] == 200, response
            assert json.loads(response["body"]) == {{"runs": []}}
            assert not any(m == "strands" or m.startswith("strands.") for m in sys.modules)
            print("ok")
    """)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stdout + result.stderr
