"""The frontend, held to the same suite as everything else.

The frontend reads committed fixtures: the API's own responses for the canonical
runs, recorded by web/scripts/record_fixtures.py through the real handlers. The
first test regenerates them and fails on any difference, so a fixture the backend
no longer emits cannot leave both suites green. The rest run the frontend's own
suites, so `make verify` can break a frontend claim and see this suite go red.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.single_backend

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"


def load_recorder():
    spec = importlib.util.spec_from_file_location("record_web_fixtures", WEB / "scripts" / "record_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_web_fixtures_are_what_the_api_returns_now():
    recorder = load_recorder()
    for name, text in recorder.produce().items():
        committed = WEB / "src" / "fixtures" / name
        assert committed.exists(), f"web/src/fixtures/{name} is missing - run web/scripts/record_fixtures.py"
        assert committed.read_text() == text, f"web/src/fixtures/{name} is stale - run web/scripts/record_fixtures.py"


def frontend(*command: str, timeout: int) -> subprocess.CompletedProcess:
    if not (WEB / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.fail("the frontend's dependencies are not installed: cd web && npm ci")
    return subprocess.run(["npx", *command], cwd=WEB, capture_output=True, text=True, timeout=timeout)


def test_the_frontend_type_checks():
    result = frontend("tsc", "-p", "tsconfig.json", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_frontend_unit_suite_passes():
    result = frontend("vitest", "run", "--reporter=dot", timeout=300)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
