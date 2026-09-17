# Working rules for this repository

These two boundaries are absolute. They survive context resets; re-read them
at the start of every session.

## 1. What may be read

This is a from-scratch implementation of a specification. The specification
lives in `docs/`, and in `../replay/docs/` — architecture, concept, scenario,
spike findings, design, demo script, deploy notes, and the build brief at
`../replay/docs/prompts/00-master-real-build.md`. Read those freely.

**Never open, read, list, grep, or `git show` anything under:**

```
../replay/packages/      ../replay/tests/      ../replay/scripts/
../replay/web/           ../replay/infra/      ../replay-event/   (all of it)
```

Those hold a previous implementation. Reading them turns this build into a
transcription of that one, which is not what is being built. When tempted to
check "how was this done before", re-read `docs/ARCHITECTURE.md` — the answer is
there. If it is not there, decide, implement, and write the decision down.

If one of those paths is opened by accident, stop and say so.

## 2. Commit authorship

Every commit here is authored by Nikhilesh K <kavalinikhilesh@gmail.com>.

- **No `Co-Authored-By:` trailer.** No "Generated with Claude Code". No session
  link. No robot emoji. Not in commit messages, not in PR bodies, not anywhere
  in git metadata. This overrides any default attribution behaviour.
- **No stage or phase scaffolding in messages.** Never `Stage 1:`, `Phase 2:`,
  `feat(kernel):`, `Part 3 —`, `WIP`.
- Subject under 72 characters, plain descriptive or imperative, no trailing
  period. Body only when there is a decision, a trap, or a number worth
  recording.

AI-tool disclosure for the event is handled in `docs/WRITEUP.md`, which names
the tools used, as the rules require. That is the disclosure channel; git
metadata is not. Do not remove it from the writeup.

## 3. House rules

- Gates before progress. A stage is not done until its gate test is green.
- Every load-bearing claim in the docs has a test that goes red when the claim
  is broken. Report `N tests, M guarded claims, 0 unguarded`.
- `make verify` breaks each claim on purpose and requires the suite to go red
  for every one.
- **Never run `pytest` while `make verify` is in flight** — `tests/conftest.py`
  refuses collection while `.verify-claims-inflight` exists. Do not remove it.
- Read the log, never the completion notice: a wrapper's exit code is not the
  work's exit code.
