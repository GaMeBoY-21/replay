"""The one boundary every store serialises through.

SQLite and DynamoDB round-trip a payload through JSON, so a dict comes back with
its keys sorted, a tuple comes back a list, and a number stored as a DynamoDB
number comes back a `Decimal`. A store that hands back the live object instead
agrees with none of that: a suite passes against it, passes against nothing
else, and the divergence appears only once deployed.

So every backend - memory included - stores the canonical JSON text of an event
and decodes it on read. All three return the recorded form, because all three
return the same thing: what `decode_event` makes of `encode_event`'s output.
Stored as text rather than as a DynamoDB map, which is also what keeps numbers
out of `Decimal`.
"""

from __future__ import annotations

import json

from replay_events import Event, RunMetadata, canonical, dump_event, parse_event


def encode_event(event: Event) -> str:
    return canonical(dump_event(event))


def decode_event(text: str) -> Event:
    return parse_event(json.loads(text))


def encode_metadata(metadata: RunMetadata) -> str:
    return canonical(metadata.model_dump(mode="json"))


def decode_metadata(text: str) -> RunMetadata:
    return RunMetadata.model_validate(json.loads(text))
