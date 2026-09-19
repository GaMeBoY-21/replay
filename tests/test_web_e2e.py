"""The frontend in a real browser, against the real local server.

Builds web/dist, then runs the Playwright suite: the canonical runs listed,
opened, traced and diffed; a fork and a resume made from the UI; a deployment
with no model showing both as unavailable; and axe on every page in both
themes at 1280x720. Separate from test_web.py so a claim can name the slower
browser suite only when it needs a browser to go red.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.single_backend

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def run(*command: str, timeout: int) -> subprocess.CompletedProcess:
    if not (WEB / "node_modules").is_dir() or shutil.which("npx") is None:
        pytest.fail("the frontend's dependencies are not installed: cd web && npm ci && npx playwright install chromium")
    return subprocess.run(command, cwd=WEB, capture_output=True, text=True, timeout=timeout)


def test_the_browser_suite_passes_against_the_local_server():
    built = run("npm", "run", "build", timeout=600)
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-3000:]
    result = run("npx", "playwright", "test", timeout=900)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
