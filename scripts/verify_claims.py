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
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BACKUPS = REPO / ".verify-claims-backups"
JOURNAL = REPO / ".verify-claims-journal"
INFLIGHT = REPO / ".verify-claims-inflight"
LOGDIR = BACKUPS / "logs"

KERNEL = "packages/replay/src/replay/kernel"
EVENTS = "packages/events/src/replay_events"
STORE = "packages/replay/src/replay/store"


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


def run_tests(claim: Claim, index: int) -> tuple[int, pathlib.Path]:
    """Run the selected tests. No pipe: a pipe would report the pipe's status."""
    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / f"{index:02d}.log"
    env = dict(os.environ)
    env["REPLAY_VERIFY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with open(logfile, "w") as out:
        completed = subprocess.run(
            ["uv", "run", "pytest", *claim.tests, "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=REPO,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
    return completed.returncode, logfile


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
