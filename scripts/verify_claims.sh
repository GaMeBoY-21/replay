#!/usr/bin/env bash
# A thin wrapper. All the logic is in verify_claims.py, where exact-string
# mutation and an fsync'd journal are safe to express.
#
# `set -euo pipefail` lives here rather than in the Makefile: GNU Make 3.81,
# which macOS ships, ignores .SHELLFLAGS silently, so a recipe that relied on it
# would be strict in CI and inert on a laptop.
set -euo pipefail

cd "$(dirname "$0")/.."

# No pipe. A pipe replaces the exit code, and this script's exit code is the
# only thing that says whether the claims are real.
exec uv run python scripts/verify_claims.py
