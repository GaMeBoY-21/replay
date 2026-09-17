"""Guards on the repository itself.

Two of these encode things that have gone wrong before and cost real time. They
are cheap, they run with the suite, and they fail at the moment the mistake is
made rather than at the moment it is noticed.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def test_gitignore_has_one_pattern_per_line():
    """CLAIM: a space-separated .gitignore line is inert.

    Git reads the whole line as a single literal pattern, so `.venv/ __pycache__/`
    matches a path named exactly that and nothing else. An ignore file written
    this way looks correct, reports nothing, and lets the first `git add` stage
    the virtualenv, the CDK output and every .pyc in the tree.
    """
    offenders = []
    for number, line in enumerate((REPO / ".gitignore").read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.search(r"\S\s+\S", stripped):
            offenders.append(f"{number}: {stripped}")
    assert not offenders, "these .gitignore lines hold more than one pattern:\n" + "\n".join(offenders)


def test_no_build_artefacts_are_tracked():
    tracked = git("ls-files").splitlines()
    bad = [
        path
        for path in tracked
        if path.endswith((".pyc", ".pyo"))
        or "__pycache__" in path
        or path.startswith((".venv/", "node_modules/", "cdk.out/"))
    ]
    assert not bad, f"build artefacts are tracked: {bad[:10]}"


def test_the_suite_does_not_modify_tracked_files():
    """A test that writes into the tree hides real changes in the noise — and,
    worse, can leave a mutation from `make verify` behind as a real edit.

    Untracked files are not a failure: new source is untracked until it is
    committed, and that is the normal state mid-change.
    """
    modified = [
        line
        for line in git("status", "--porcelain").splitlines()
        if not line.startswith("??")
    ]
    assert not modified, f"the suite modified tracked files: {modified}"


def test_no_commit_carries_a_generated_by_trailer():
    """CLAIM: commit authorship in this repository is mine.

    Tool disclosure for the event lives in docs/WRITEUP.md, which is where the
    rules require it. Git metadata is not the disclosure channel, and a trailer
    that drifts in from a tool default is noise in the history.
    """
    log = git("log", "--format=%B%n%an <%ae>")
    banned = re.compile(
        r"(?im)^(co-authored-by:|signed-off-by:)|generated with \[?claude|claude\.ai/code|🤖"
    )
    found = banned.findall(log)
    assert not found, f"a commit carries an attribution trailer: {found}"


def test_every_commit_is_mine():
    authors = set(git("log", "--format=%an <%ae>").splitlines())
    assert authors == {"Nikhilesh K <kavalinikhilesh@gmail.com>"}, authors


def test_the_working_rules_are_committed():
    """CLAUDE.md is the thing that survives a context reset. If it is missing,
    the next session has no boundaries."""
    rules = (REPO / "CLAUDE.md").read_text()
    assert "Co-Authored-By" in rules
    assert "../replay/packages/" in rules
