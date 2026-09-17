# Recipes are SINGLE commands. Anything with more than one step lives in
# scripts/, under `set -euo pipefail`.
#
# .SHELLFLAGS is set for the Make versions that honour it, but is NOT relied on:
# GNU Make 3.81 — which macOS ships — ignores it silently, so a recipe written
# to depend on it is fail-fast in CI and inert on a laptop.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: test verify sync

sync:   ; uv sync --all-packages
# No pipe into anything: a pipe replaces the exit code.
test:   ; uv run pytest
# Break each load-bearing claim on purpose; the suite must go red for every one.
verify: ; ./scripts/verify_claims.sh
