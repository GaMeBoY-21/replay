"""Write the JSON Schema of the event log, for the frontend's generated types.

    uv run python web/scripts/schema.py   ->  web/src/types/schema.json

`npm run types` turns it into web/src/types/events.d.ts. Both are generated on
every build and never committed, so the frontend's event types cannot drift from
the Pydantic models the backend writes.
"""

from __future__ import annotations

import json
import pathlib

from pydantic import TypeAdapter

from replay_events import Event, RunMetadata

OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "types" / "schema.json"


def main() -> None:
    event = TypeAdapter(Event).json_schema(ref_template="#/$defs/{model}")
    metadata = RunMetadata.model_json_schema(ref_template="#/$defs/{model}")
    defs = {**event.pop("$defs", {}), **metadata.pop("$defs", {})}
    defs["ReplayEvent"] = {**event, "title": "ReplayEvent"}
    defs["RunMetadata"] = metadata
    # A root that refers to both, so the generator emits both as named types.
    schema = {
        "title": "ReplaySchema",
        "type": "object",
        "properties": {"event": {"$ref": "#/$defs/ReplayEvent"}, "metadata": {"$ref": "#/$defs/RunMetadata"}},
        "$defs": defs,
    }
    OUT.write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
