# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The merge-state read: one gh answer, or None when it cannot be trusted."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import pr_mergeable


def _gh(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(pr_mergeable.subprocess, "run", run)
    monkeypatch.setattr(pr_mergeable, "gh_binary", lambda: "gh")
    return calls


@pytest.mark.parametrize("state", ["MERGEABLE", "CONFLICTING", "UNKNOWN"])
def test_each_known_state_is_returned(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    calls = _gh(monkeypatch, 0, f'{{"mergeable": "{state}"}}')
    assert pr_mergeable.gh_pull_request_mergeable(Path(), 7, "o", "r") == state
    assert calls[0][1:] == ["pr", "view", "7", "--json", "mergeable", "--repo", "o/r"]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, '{"mergeable": "CONFLICTING"}'),
        (0, '{"mergeable": "BOGUS"}'),
        (0, '{"mergeable": null}'),
        (0, "{}"),
        (0, "not json"),
        (0, "[1]"),
    ],
    ids=["gh-failed", "unknown-value", "null", "no-field", "not-json", "not-object"],
)
def test_a_failed_or_unknown_answer_is_unread(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> None:
    _gh(monkeypatch, returncode, stdout)
    assert pr_mergeable.gh_pull_request_mergeable(Path(), 7, "o", "r") is None


def test_a_missing_repository_is_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _gh(monkeypatch, 0, '{"mergeable": "MERGEABLE"}')
    assert pr_mergeable.gh_pull_request_mergeable(Path(), 7, None, "r") is None
    assert calls == []
