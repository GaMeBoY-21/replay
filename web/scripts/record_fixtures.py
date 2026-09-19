"""Record the frontend's fixtures from the real store, through the real API.

    uv run python web/scripts/record_fixtures.py          # write
    uv run python web/scripts/record_fixtures.py --check  # fail if anything differs

The canonical runs are loaded into a store and every GET the frontend makes is
sent through the same `dispatch` the local server and the Lambda call. Each
response is committed exactly as the API returned it, so the frontend in fixture
mode reads what the API emits, not a hand-made approximation of it.

Two records are not API responses and are bundled as data: the canonical
manifest (which run plays which role, and where the fork's substituted response
came from) and the corpus (every run of the task, with what each run said about
its currency and what its log shows it had read when it said it).

tests/test_web_fixtures_are_current.py runs --check, so a fixture the backend no
longer emits fails the Python suite rather than leaving both suites green.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from urllib.parse import parse_qsl, urlsplit

from replay.api import dispatch
from replay.api.app import http_event
from replay.projector.projector import rebuild_all
from replay.scenario import load
from replay.scenario.corpus import load_run, run_files
from replay.scenario.live import classify
from replay.store import MemoryLogStore
from replay.store.views import MemoryViewStore
from replay_events import EffectCompleted, EffectRequested, MemoryWrite
from replay.kernel import resolve, step_index

REPO = pathlib.Path(__file__).resolve().parents[2]
CANONICAL = REPO / "fixtures" / "canonical"
CORPUS = REPO / "fixtures" / "corpus-qwen2.5-14b"
OUT = REPO / "web" / "src" / "fixtures"

# Tools whose results are the evidence an agent has about the invoice.
EVIDENCE = ("get_invoice_header", "get_line_items", "get_remittance_details", "lookup_vendor")


def api_requests(runs: list[str], manifest: dict) -> list[str]:
    # Recorded with no runner: a recording cannot run the agent, and says so.
    paths = ["/api/runs", "/api/capabilities"]
    for run_id in runs:
        paths += [f"/api/runs/{run_id}", f"/api/runs/{run_id}/events?limit=1000",
                  f"/api/runs/{run_id}/trace/output"]
    wrong, right, fork = manifest["wrong"]["run_id"], manifest["right"]["run_id"], manifest["fork"]["run_id"]
    paths += [f"/api/diff?a={wrong}&b={fork}", f"/api/diff?a={wrong}&b={right}"]
    return paths


def call(path: str, store, views) -> dict:
    parts = urlsplit(path)
    response = dispatch(http_event("GET", parts.path, query=dict(parse_qsl(parts.query))),
                        store=store, views=views, runner=None)
    return {"status": response["statusCode"], "body": json.loads(response["body"])}


def tool_result_json(event: EffectCompleted):
    """A tool's recorded result, parsed back from the text the model was shown."""
    try:
        return json.loads(event.result.value["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def decisive_currency_write(events):
    """The currency write the run's answer depends on: the one its last conversion
    read, or - for a run that never converted - the last one it made."""
    by_eid = {e.eid: e for e in events}
    writes = [e for e in events if isinstance(e, MemoryWrite) and e.key == "invoice.currency"]
    totals = [e for e in events if isinstance(e, MemoryWrite) and e.key == "report.total"]
    if totals:
        for read_eid in totals[-1].reads:
            read = by_eid.get(read_eid)
            if getattr(read, "key", None) == "invoice.currency" and read.source is not None:
                return by_eid[read.source]
    return writes[-1] if writes else None


def basis_of(events, write) -> str | None:
    """The basis recorded beside a currency write: the next basis write after it."""
    if write is None:
        return None
    return next((e.value for e in events if isinstance(e, MemoryWrite)
                 and e.key == "invoice.currency.basis" and e.eid > write.eid), None)


def evidence_before_currency(events) -> dict:
    """What the run had read when it recorded the currency, from its log alone."""
    steps = step_index(events)
    write = decisive_currency_write(events)
    cutoff = write.eid if write is not None else None
    requested = {e.seq: e for e in events if isinstance(e, EffectRequested)}
    read = []
    for event in events:
        if cutoff is not None and event.eid > cutoff:
            break
        if isinstance(event, EffectCompleted) and event.seq in requested:
            name = getattr(requested[event.seq].effect, "name", None)
            if name in EVIDENCE:
                read.append({"step": steps[requested[event.seq].eid], "tool": name,
                             "result": tool_result_json(event)})
    return {"read": read, "currency_write_step": steps[cutoff] if cutoff is not None else None}


def corpus() -> dict:
    stats = json.loads((CORPUS / "stats.json").read_text())
    provider = json.loads((CORPUS / "provider.json").read_text())
    rows = []
    for path in run_files(CORPUS):
        metadata, events = load_run(path)
        row = classify(events, metadata)
        overall = next(r["overall"] for r in stats["rows"] if r["run_id"] == metadata.run_id)
        rows.append({
            "run_id": row["run_id"],
            "overall": overall,
            "steps": row["steps"],
            "currencies_recorded": row["currencies_recorded"],
            "currency_used": row["currency_used"],
            "assumption_step": row["assumption_step"],
            "assumption_basis": row["assumption_basis"],
            "converted_total": row["converted_total"],
            "submitted": row["submitted"],
            "vendor_lookups": row["vendor_lookups"],
            "answer": row["answer"],
            "basis_stated": basis_of(events, decisive_currency_write(events)),
            **evidence_before_currency(events),
        })
    return {"provider": provider, "runs": stats["runs"], "overall": stats["overall"],
            "steps": stats["steps"], "vendor_lookups": stats["vendor_lookups"],
            "fetched_remittance": stats["fetched_remittance"], "rows": rows}


def with_steps(manifest: dict, store) -> dict:
    """The manifest, with the step number beside each seq it names. The UI shows
    steps and never constructs a seq; the mapping comes from the log."""
    def step_of(run_id: str, seq: int) -> int:
        events = resolve(store, run_id).events
        steps = step_index(events)
        return next(steps[e.eid] for e in events if isinstance(e, EffectRequested) and e.seq == seq)

    fork = manifest["fork"]
    source = fork["substituted_from"]
    return {**manifest, "fork": {**fork, "at_step": step_of(fork["run_id"], fork["at_seq"]),
                                 "substituted_from": {**source, "step": step_of(source["run_id"], source["seq"])}}}


def produce() -> dict[str, str]:
    store, views = MemoryLogStore(), MemoryViewStore()
    manifest = with_steps(load(store, CANONICAL), store)
    rebuild_all(store, views)
    runs = sorted(m.run_id for m in store.list_runs())
    responses = {path: call(path, store, views) for path in api_requests(runs, manifest)}
    files = {"api.json": responses, "manifest.json": manifest, "corpus.json": corpus()}
    return {name: json.dumps(value, indent=1, sort_keys=True) + "\n" for name, value in files.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    produced = produce()
    if args.check:
        stale = [name for name, text in produced.items()
                 if not (OUT / name).exists() or (OUT / name).read_text() != text]
        if stale:
            print("stale web fixtures: " + ", ".join(stale) + " - run web/scripts/record_fixtures.py")
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in produced.items():
        (OUT / name).write_text(text)
        print(f"wrote web/src/fixtures/{name} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
