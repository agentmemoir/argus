# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The argv the check read is built from, against a real gh on ``PATH``.

The boundary here is a program, not a monkeypatched function: the shim records
every argv it was given and answers one check list, so the test can only pass by
the code starting the command a person would run by hand. The three cases are
the three the caller can be in: no repository named (gh answers about ``root``),
an explicit ``owner/repo``, and a non-default host.

``gh pr checks`` takes no ``--hostname``: ``-R/--repo [HOST/]OWNER/REPO`` is the
one host-bearing argument, so the host has to travel inside it.
"""

import atexit
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.exec_tempdir import exec_tempdir
from argus.gitsync.gh_checks import gh_pull_request_checks

CHECKS = ["pr", "checks", "7", "--json", "bucket,state,name,link"]

SHIM = """#!{python}
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_GH_STATE"])
args = sys.argv[1:]
with (state / "argv.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
print(json.dumps([{{"bucket": "pass", "state": "SUCCESS",
                   "name": "verify", "link": ""}}]))
sys.exit(0)
"""


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gh command name the check read starts, before the shim is on PATH."""
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


@pytest.fixture
def shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake gh on PATH that records its argv, and a root to run it in."""
    state = tmp_path / "gh"
    state.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    # A shim in the default temporary directory is not enough: a host that
    # mounts it noexec makes execvp skip it and run the host's gh, which would
    # measure the machine instead of the code.
    executable = exec_tempdir(purpose="the checks argv test's fake gh")
    atexit.register(executable.cleanup)
    binary = Path(executable.name) / "gh"
    binary.write_text(SHIM.format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("gh") == str(binary), "the host's gh answered, not the shim"
    return root


def _argvs(root: Path) -> list[list[str]]:
    lines = (root.parent / "gh" / "argv.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in lines.splitlines()]


@pytest.mark.parametrize(
    ("owner", "repo", "missing"),
    [
        (None, None, "owner and repo"),
        ("example-org", None, "repo"),
        (None, "example-repo", "owner"),
    ],
)
def test_a_missing_repository_is_refused_and_gh_is_not_run(
    shim: Path, owner: str | None, repo: str | None, missing: str
) -> None:
    # The shim writes its argv when it runs, so a missing file means gh was
    # never started. Before the refusal existed, a partial reference ran gh
    # about the repository of the working directory.
    rows, code, detail = gh_pull_request_checks(shim, 7, owner, repo)

    assert not (shim.parent / "gh" / "argv.jsonl").exists()
    assert (rows, code) == (None, -1)
    assert f"{missing} missing" in detail


def test_an_explicit_repository_is_passed_as_repo(shim: Path) -> None:
    rows, code, _detail = gh_pull_request_checks(shim, 7, "example-org", "example-repo")

    assert _argvs(shim) == [CHECKS + ["--repo", "example-org/example-repo"]]
    assert rows is not None and code == 0


def test_github_com_is_gh_own_default_and_is_not_named(shim: Path) -> None:
    gh_pull_request_checks(shim, 7, "example-org", "example-repo", "github.com")

    assert _argvs(shim) == [CHECKS + ["--repo", "example-org/example-repo"]]


def test_a_non_default_host_rides_inside_the_repo_argument(shim: Path) -> None:
    # The full argv, not a substring: the host must appear in the one argument
    # gh accepts it in, and nowhere else.
    gh_pull_request_checks(shim, 7, "example-org", "example-repo", "ghe.example.com")

    assert _argvs(shim) == [
        CHECKS + ["--repo", "ghe.example.com/example-org/example-repo"]
    ]
