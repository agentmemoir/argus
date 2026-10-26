# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The git version check refuses git older than 2.38 and accepts 2.38 or newer."""

import subprocess

import pytest

from argus.gitsync import git_version

NEEDS = "2.38 or newer is needed for merge-tree --write-tree"


@pytest.mark.parametrize(
    ("output", "line", "accepted"),
    [
        (
            "git version 2.37.1\n",
            f"git version: REFUSED (found 2.37.1; {NEEDS})",
            False,
        ),
        ("git version 2.38.0\n", "git version: accepted (2.38.0)", True),
        ("git version 2.38\n", "git version: accepted (2.38)", True),
        (
            "git version 2.39.5 (Apple Git-154)\n",
            "git version: accepted (2.39.5)",
            True,
        ),
        ("git version 2.38.0.windows.1\n", "git version: accepted (2.38.0)", True),
        ("git version 3.0.0\n", "git version: accepted (3.0.0)", True),
        (
            "",
            f"git version: REFUSED (git --version printed no version; {NEEDS})",
            False,
        ),
    ],
)
def test_classify_names_the_version_found_and_the_one_needed(
    output: str, line: str, accepted: bool
) -> None:
    assert git_version.classify(output) == (accepted, line)
    assert "\n" not in line


def test_probe_refuses_a_git_that_cannot_be_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("no git")

    monkeypatch.setattr(git_version.subprocess, "run", missing)
    accepted, line = git_version.probe("git")
    assert not accepted
    assert line == f"git version: REFUSED (git --version could not be run; {NEEDS})"


def test_main_prints_one_line_and_exits_by_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = {"old": "git version 2.37.1\n", "new": "git version 2.38.0\n"}

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, printed[argv[0]], "")

    monkeypatch.setattr(git_version.subprocess, "run", fake_run)
    assert git_version.main(["old"]) == 1
    assert git_version.main(["new"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"git version: REFUSED (found 2.37.1; {NEEDS})",
        "git version: accepted (2.38.0)",
    ]
