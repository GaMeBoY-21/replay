"""Call the bundled `api` handler locally, against moto, before any deploy.

    uv run python scripts/invoke_bundle.py

Catches the "every request 502s" class of bug - an import the bundle lacks, a
handler path that is wrong, a region default - in seconds rather than after a
CloudFront deploy. The handler, the router, the stores and pydantic are all
imported from .scratch/lambda-bundle/, not from the workspace.

The one exception is pydantic_core's compiled extension: the bundle holds the
Linux aarch64 build, which cannot load on this machine. So this checks that the
bundled binary is the Linux aarch64 build of exactly the version the workspace
runs, and pre-loads the workspace's build of that same version. boto3 comes from
the workspace too, as the Lambda runtime provides its own.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = REPO / ".scratch" / "lambda-bundle"
REGION = "ap-south-1"


def main() -> int:
    if not BUNDLE.is_dir():
        sys.exit("no bundle - run scripts/bundle_lambda.sh first")

    import pydantic_core  # the host build, before the bundle goes on the path

    binary = next((BUNDLE / "pydantic_core").glob("_pydantic_core.*.so"))
    assert "aarch64-linux-gnu" in binary.name and "cpython-312" in binary.name, binary.name
    bundled = next(BUNDLE.glob("pydantic_core-*.dist-info")).name.split("-")[1].removesuffix(".dist")
    assert bundled == pydantic_core.__version__, f"bundle has pydantic_core {bundled}, host {pydantic_core.__version__}"

    for name in [m for m in sys.modules if m.split(".")[0] in {"replay", "replay_events", "pydantic"}]:
        del sys.modules[name]
    sys.path.insert(0, str(BUNDLE))
    os.environ.update(AWS_REGION=REGION, EVENTS_TABLE="replay-events", VIEWS_TABLE="replay-views",
                      PAYLOAD_BUCKET="replay-payloads", AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")

    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        started = time.monotonic()
        from replay.aws.api import handler
        from replay.aws.projector import handler as project
        imported = time.monotonic() - started
        # What the Lambda imports, and nothing more: no Strands. The seeding
        # helpers below import the scenario package, which the Lambda never does.
        assert not any(m == "strands" or m.startswith("strands.") for m in sys.modules), "the entry points import strands"
        from replay.scenario.canonical import load
        from replay.store.dynamo import create_event_table
        from replay.store.views import create_view_table

        import replay
        import replay_events
        import pydantic

        for module in (replay, replay_events, pydantic):
            assert pathlib.Path(module.__file__).is_relative_to(BUNDLE), f"{module.__name__} came from {module.__file__}"

        create_event_table(client, "replay-events")
        create_view_table(client, "replay-views")
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="replay-payloads", CreateBucketConfiguration={"LocationConstraint": REGION})
        from replay.aws.resources import log_store

        load(log_store(), REPO / "fixtures" / "canonical")
        runs = [m.run_id for m in log_store().list_runs()]
        project({"Records": [{"dynamodb": {"Keys": {"PK": {"S": f"RUN#{r}"}}}} for r in runs]})

        event = {"version": "2.0", "rawPath": "/api/runs", "rawQueryString": "",
                 "requestContext": {"http": {"method": "GET", "path": "/api/runs"}, "stage": "$default"},
                 "headers": {"host": "example.cloudfront.net"}, "isBase64Encoded": False}
        response = handler(event)
        body = json.loads(response["body"])
        assert response["statusCode"] == 200, response
        assert {r["run_id"] for r in body["runs"]} == set(runs), "every seeded run is listed"

    print(f"bundled api handler: GET /api/runs -> {response['statusCode']}, {len(body['runs'])} runs listed")
    print(f"import of the bundled entry points: {imported * 1000:.0f} ms; they do not load strands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
