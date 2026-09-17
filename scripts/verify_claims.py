"""Break each load-bearing claim on purpose. The suite must go red for every one.

A test suite that is green proves the tests pass. It does not prove the tests
would notice if the code were wrong, and a claim whose property nothing in it
causes will sit there passing forever. This harness mutates one line at a time
and requires the suite to fail. A mutation that leaves the suite green is
reported as an unguarded claim — either the test is wrong or the claim is.

Four things here look like paranoia and are not. Each of them has let a
verification lie before:

1. **Journal before mutating.** A trap cannot catch SIGKILL. If the process is
   killed between applying a mutation and restoring it, the mutation is left in
   the tree as a real edit — and if the mutation disabled the divergence
   detector, everything afterwards is green for the wrong reason. The journal is
   written and flushed to disk *before* the edit, and replayed on next start.

2. **Purge bytecode.** CPython caches on (mtime, size). Mutate and restore
   inside one second and the cached bytecode runs instead of the mutated source,
   so the mutation test passes against the original code and reports a guard
   that was never exercised.

3. **No pipes.** `cmd | tee` reports tee's exit status. Output goes to a file
   through a redirect and the return code is read from the process.

4. **Read the log, not the notice.** Nothing here reports success from a
   wrapper's exit code.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
BACKUPS = REPO / ".verify-claims-backups"
JOURNAL = REPO / ".verify-claims-journal"
INFLIGHT = REPO / ".verify-claims-inflight"
LOGDIR = BACKUPS / "logs"

KERNEL = "packages/replay/src/replay/kernel"
EVENTS = "packages/events/src/replay_events"
STORE = "packages/replay/src/replay/store"
AGENT = "packages/replay/src/replay/agent"
ROOT_PKG = "packages/replay/src/replay"


@dataclasses.dataclass(frozen=True)
class Claim:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


CLAIMS: list[Claim] = [
    Claim(
        "the divergence detector actually fires",
        f"{KERNEL}/kernel.py",
        "    if recorded.shape() != live.shape():",
        "    if False:",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "replay serves the log and executes nothing",
        f"{KERNEL}/kernel.py",
        '    if ctx.mode.kind == "replay" and seq <= ctx.mode.up_to:\n'
        "        return Begun(seq, live=False, result=serve_recorded(ctx, seq, effect))",
        '    if ctx.mode.kind == "replay" and seq <= ctx.mode.up_to:\n        pass',
        ("tests/test_gate_kernel.py",),
    ),
    Claim(
        "eid is unique and monotonic over every event",
        f"{KERNEL}/context.py",
        "        base = self._eid\n        self._eid += count\n        return base",
        "        return 0",
        ("tests/test_determinism.py",),
    ),
    Claim(
        "the read-set clears at a step boundary",
        f"{KERNEL}/context.py",
        "        self.pending_reads.clear()\n        return eid",
        "        return eid",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "snapshot_reads copies rather than aliasing",
        f"{KERNEL}/context.py",
        "        return list(self.pending_reads)",
        "        return self.pending_reads",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "Begun.live is the flag, not the payload",
        f"{KERNEL}/kernel.py",
        "    begun = begin_effect(ctx, effect)\n"
        "    if not begun.live:\n"
        "        return begun.result\n"
        "    return complete_effect(ctx, begun.seq, execute())",
        # The naive spelling of this bug is not `begun.result is not None` —
        # `result` is a Result wrapper and is None exactly when the effect is
        # live, so that spelling is accidentally correct. The bug that bites is
        # reaching *through* the wrapper to the payload, which a recorded effect
        # is allowed to have as None. The first mutation passed green and that
        # is what surfaced the distinction.
        "    begun = begin_effect(ctx, effect)\n"
        "    if begun.result is not None and begun.result.value is not None:\n"
        "        return begun.result\n"
        "    return complete_effect(ctx, begun.seq, execute())",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "append is conditional on the eid being free",
        f"{STORE}/memory.py",
        "        if event.eid in run:\n"
        '            raise EventIdConflict(f"{run_id}: eid {event.eid} is already taken")',
        "        pass",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "the store exposes no mutate path",
        f"{STORE}/memory.py",
        "    def list_runs(self)",
        "    def update(self, run_id, event):\n        pass\n\n    def list_runs(self)",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "breakers rebuild their counters during replay",
        f"{KERNEL}/breakers.py",
        '        """Replay: advance the counters, never trip."""\n        self._count(effect)',
        '        """Replay: advance the counters, never trip."""',
        ("tests/test_breakers.py",),
    ),
    Claim(
        "the model fingerprint covers tool_choice",
        f"{EVENTS}/effects.py",
        '                "tool_choice": self.tool_choice,\n',
        "",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "the model fingerprint covers system_prompt_content",
        f"{EVENTS}/effects.py",
        '                "system_prompt_content": self.system_prompt_content,\n',
        "",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "volatile message fields are stripped before comparing",
        f"{EVENTS}/effects.py",
        "                if k not in VOLATILE_MESSAGE_FIELDS",
        "                if True",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "one seq is never closed twice",
        f"{KERNEL}/log.py",
        "                if record.completed is not None:",
        "                if False:",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "state_at honours tombstones",
        f"{KERNEL}/state.py",
        "        if event.tombstone:",
        "        if False:",
        ("tests/test_kernel_invariants.py",),
    ),
    Claim(
        "the .gitignore guard notices a multi-pattern line",
        ".gitignore",
        ".venv/\nvenv/",
        ".venv/ venv/",
        ("tests/test_repo_hygiene.py",),
    ),
    Claim(
        "replay swaps in the recorded tool, so the real one never runs",
        f"{AGENT}/hooks.py",
        "        event.selected_tool = RecordedTool(effect.name, spec, begun.result)",
        "        _unused = RecordedTool(effect.name, spec, begun.result)",
        ("tests/test_gate_strands.py",),
    ),
    Claim(
        "the recorded tool yields a bare ToolResult",
        f"{AGENT}/hooks.py",
        "        yield self._recorded.value",
        '        yield {"toolResult": self._recorded.value}',
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "a live tool effect is closed when its result arrives",
        f"{AGENT}/hooks.py",
        "            complete_effect(self.ctx, seq, Result(value=event.result, error=error))",
        "            pass",
        ("tests/test_gate_strands.py",),
    ),
    Claim(
        "a tool's duration never reaches the replayable log",
        f"{AGENT}/hooks.py",
        "            complete_effect(self.ctx, seq, Result(value=event.result, error=error))",
        '            complete_effect(self.ctx, seq, Result(value={**event.result, "duration": event.duration}, error=error))',
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "a tool breaker cancels the call before the tool runs",
        f"{AGENT}/hooks.py",
        '            event.cancel_tool = f"halted by the {trip.name} breaker: {trip.detail}"',
        "            pass",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "ReplayModel fingerprints tool_choice",
        f"{AGENT}/model.py",
        "        system_prompt=system_prompt,\n        tool_choice=tool_choice,\n",
        "        system_prompt=system_prompt,\n",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "ReplayModel fingerprints system_prompt_content",
        f"{AGENT}/model.py",
        "        tool_choice=tool_choice,\n        system_prompt_content=system_prompt_content,\n    )",
        "        tool_choice=tool_choice,\n    )",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "structured_output is gated, not inherited",
        f"{AGENT}/model.py",
        "        raise NotImplementedError(\n"
        '            "structured_output is not recorded by Replay; use tool calls through stream instead"\n'
        "        )",
        "        return None",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "the sequential executor replaces the concurrent default",
        f"{AGENT}/wiring.py",
        "    agent.tool_executor = SequentialToolExecutor()",
        "    pass",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "agent.state is wrapped, so tool reads and writes are recorded",
        f"{AGENT}/wiring.py",
        "    agent.state = state\n",
        "    pass\n",
        ("tests/test_strands_seam.py",),
    ),
    Claim(
        "replayed state is seeded from the log, because tools do not run",
        f"{AGENT}/wiring.py",
        "        seed_state(inner, ctx.log.events, before_eid=cut)",
        "        pass",
        ("tests/test_gate_strands.py",),
    ),
    Claim(
        "token usage is read from a recorded chunk list",
        f"{KERNEL}/breakers.py",
        '        return sum(\n            _usage_tokens(chunk["metadata"].get("usage"))',
        '        return 0 * sum(\n            _usage_tokens(chunk["metadata"].get("usage"))',
        ("tests/test_breaker_halts.py",),
    ),
    Claim(
        "a model-side ceiling is checked before the call, so the halt ends cleanly",
        f"{AGENT}/hooks.py",
        "                    self.ctx.breakers.check_ceilings(seq)",
        "                    pass",
        ("tests/test_breaker_halts.py",),
    ),
    Claim(
        "a model-side halt is appended to the log",
        f"{AGENT}/hooks.py",
        "                    record_trip(self.ctx, self.ctx.next_seq(), trip)",
        "                    pass",
        ("tests/test_breaker_halts.py",),
    ),
    Claim(
        "a non-empty model_state is refused as an unrecorded input",
        f"{AGENT}/model.py",
        '    if kwargs.get("model_state"):',
        "    if False:",
        ("tests/test_breaker_halts.py",),
    ),
    Claim(
        "a non-empty agent_metadata is refused as an unrecorded input",
        f"{AGENT}/model.py",
        "        if any(value is not None for value in values.values()):",
        "        if False:",
        ("tests/test_breaker_halts.py",),
    ),
    Claim(
        "the forked step belongs to the fork, not the parent",
        f"{KERNEL}/chain.py",
        "        prefix = events if cut is None else [e for e in events if e.eid < cut]",
        "        prefix = events if cut is None else [e for e in events if e.eid <= cut]",
        ("tests/test_gate_fork.py",),
    ),
    Claim(
        "a fork reads its prefix from its parent",
        f"{KERNEL}/chain.py",
        "        events = prefix + store.read(child)",
        "        events = store.read(child)",
        ("tests/test_gate_fork.py",),
    ),
    Claim(
        "a cycle in the fork chain is refused, and a runaway dies under the cap",
        f"{KERNEL}/chain.py",
        "        if current in seen:",
        "        if False:",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "a replayed model step appends nothing to the fork's own log",
        f"{AGENT}/model.py",
        "        if self.ctx.next_eid != before:",
        "        if True:",
        ("tests/test_gate_fork.py",),
    ),
    Claim(
        "a replayed tool step appends nothing to the fork's own log",
        f"{AGENT}/hooks.py",
        "        if started is None or self.ctx.next_eid != started:",
        "        if True:",
        ("tests/test_gate_fork.py",),
    ),
    Claim(
        "a fork continues its parent's eid sequence",
        f"{AGENT}/runs.py",
        "    eid_base = parent.next_eid",
        "    eid_base = 0",
        ("tests/test_gate_fork.py",),
    ),
    Claim(
        "lineage is written before the first event",
        f"{AGENT}/runs.py",
        "    store.put_metadata(metadata)\n    try:",
        "    try:",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "a fork is seeded with state as of the fork point",
        f"{AGENT}/wiring.py",
        "        seed_state(inner, ctx.log.events, before_eid=cut)",
        "        seed_state(inner, ctx.log.events, before_eid=None)",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "a fork's writer index stops at the fork point",
        f"{AGENT}/wiring.py",
        "        ctx.writer_of = writer_index(ctx.log.events, before_eid=cut)",
        "        pass",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "resume applies its breaker overrides",
        f"{AGENT}/runs.py",
        "    config = (breaker_config or BreakerConfig()).overridden(breaker_overrides)",
        "    config = (breaker_config or BreakerConfig()).overridden(None)",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "resume continues its parent's eid sequence",
        f"{AGENT}/runs.py",
        "    eid_base = log.next_eid",
        "    eid_base = 0",
        ("tests/test_fork_chain.py",),
    ),
    Claim(
        "run ids are monotonic within a process",
        f"{ROOT_PKG}/ids.py",
        "        if now <= _last[0]:",
        "        if False:",
        ("tests/test_fork_chain.py",),
    ),
]


# ----------------------------------------------------------------- journal


def journal_write(entries: list[dict]) -> None:
    """Write the journal and flush it to the platter before anything is edited."""
    with open(JOURNAL, "w") as handle:
        json.dump(entries, handle)
        handle.flush()
        os.fsync(handle.fileno())


def journal_replay() -> None:
    """Restore anything a killed run left behind."""
    if not JOURNAL.exists():
        return
    try:
        entries = json.loads(JOURNAL.read_text() or "[]")
    except json.JSONDecodeError:
        entries = []
    for entry in entries:
        backup = pathlib.Path(entry["backup"])
        target = REPO / entry["path"]
        if backup.exists():
            shutil.copyfile(backup, target)
            print(f"  restored {entry['path']} from a previous run that did not finish")
    JOURNAL.unlink(missing_ok=True)


def purge_bytecode() -> None:
    """CPython caches on (mtime, size); a sub-second mutate/restore cycle is
    invisible to it and the stale bytecode runs instead."""
    for cache in REPO.rglob("__pycache__"):
        if ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)


# ------------------------------------------------------------------- run


# A mutation can turn a bounded walk into an unbounded one. That should end as a
# failed test - the result wanted anyway - not take the machine with it. The
# conftest guard stops a CONCURRENT pytest; nothing else stopped a runaway inside
# the harness itself.
MEMORY_CAP_BYTES = 2 * 2**30
TIMEOUT_S = 600


def _cap_address_space() -> None:
    """Runs in the child before exec. Linux enforces RLIMIT_AS; macOS refuses to
    set it at all, which is why the watchdog in `run_tests` exists too."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_CAP_BYTES, MEMORY_CAP_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def _group_rss_bytes(pgid: int) -> int:
    """Resident memory of every process in a process group: `uv` and the pytest
    it spawns are separate processes, and the runaway is the grandchild."""
    listing = subprocess.run(["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True).stdout
    total_kb = 0
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid) and parts[1].isdigit():
            total_kb += int(parts[1])
    return total_kb * 1024


def run_tests(claim: Claim, index: int) -> tuple[int, pathlib.Path]:
    """Run the selected tests under a memory cap and a wall-clock limit.

    No pipe: a pipe would report the pipe's status. The child gets its own
    process group, so a kill reaches every process it started.
    """
    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / f"{index:02d}.log"
    env = dict(os.environ)
    env["REPLAY_VERIFY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    killed = None
    with open(logfile, "w") as out:
        child = subprocess.Popen(
            ["uv", "run", "pytest", *claim.tests, "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=REPO,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            preexec_fn=_cap_address_space,
        )
        started = time.monotonic()
        while child.poll() is None:
            time.sleep(0.25)
            rss = _group_rss_bytes(child.pid)
            if rss > MEMORY_CAP_BYTES:
                killed = f"exceeded the {MEMORY_CAP_BYTES // 2**20} MiB memory cap ({rss // 2**20} MiB)"
            elif time.monotonic() - started > TIMEOUT_S:
                killed = f"exceeded the {TIMEOUT_S}s time limit"
            if killed:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                break
    if killed:
        with open(logfile, "a") as out:
            out.write(f"\nverify_claims: killed the test run - it {killed}\n")
        # A runaway is the suite failing, which is what a guarded claim needs.
        return 137, logfile
    return child.returncode, logfile


def main() -> int:
    print("Breaking each claim on purpose. The suite must go red for every one.\n")
    BACKUPS.mkdir(exist_ok=True)
    journal_replay()
    INFLIGHT.write_text("verify_claims is mutating source\n")

    unguarded: list[str] = []
    missing: list[str] = []

    try:
        for index, claim in enumerate(CLAIMS, start=1):
            target = REPO / claim.path
            source = target.read_text()

            if claim.old not in source:
                missing.append(claim.name)
                print(f"[{index:2d}/{len(CLAIMS)}] SKIP  {claim.name}")
                print(f"         the mutation target is gone from {claim.path}; "
                      "the claim is unverified, not proven")
                continue
            if source.count(claim.old) != 1:
                missing.append(claim.name)
                print(f"[{index:2d}/{len(CLAIMS)}] SKIP  {claim.name}")
                print(f"         the mutation target appears {source.count(claim.old)} times; "
                      "widen it until it is unique")
                continue

            backup = BACKUPS / f"{index:02d}-{target.name}"
            shutil.copyfile(target, backup)
            journal_write([{"path": claim.path, "backup": str(backup)}])

            try:
                target.write_text(source.replace(claim.old, claim.new, 1))
                purge_bytecode()
                code, logfile = run_tests(claim, index)
            finally:
                shutil.copyfile(backup, target)
                purge_bytecode()
                JOURNAL.unlink(missing_ok=True)

            if code == 0:
                unguarded.append(claim.name)
                print(f"[{index:2d}/{len(CLAIMS)}] GREEN {claim.name}")
                print(f"         the suite passed with this broken. See {logfile}")
            else:
                print(f"[{index:2d}/{len(CLAIMS)}] red   {claim.name}")
    finally:
        INFLIGHT.unlink(missing_ok=True)

    print()
    guarded = len(CLAIMS) - len(unguarded) - len(missing)
    print(f"{guarded}/{len(CLAIMS)} claims guarded")

    if missing:
        print("\nUNVERIFIED — the mutation no longer applies:")
        for name in missing:
            print(f"  - {name}")
    if unguarded:
        print("\nUNGUARDED — broken on purpose and nothing noticed:")
        for name in unguarded:
            print(f"  - {name}")
        print("\nEither the test does not test what it says, or the claim is not true.")

    return 1 if (unguarded or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
