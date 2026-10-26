# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The head read: only a full SHA from a successful gh call counts."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.gitsync import pr_head

SHA = "c" * 40


def _gh(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int = 0,
    stdout: str = "",
    raises: BaseException | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> Any:
        calls.append(argv)
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(pr_head.subprocess, "run", run)
    monkeypatch.setattr(pr_head, "gh_binary", lambda: "gh")
    return calls


def test_a_full_sha_from_gh_is_the_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _gh(monkeypatch, stdout=f'{{"headRefOid": "{SHA}"}}')
    assert pr_head.gh_pull_request_head(tmp_path, "7", "o", "r") == SHA
    assert calls[0][1:4] == ["pr", "view", "7"]
    assert "headRefOid" in calls[0]


@pytest.mark.parametrize(
    "stdout", ["not json", "[]", '{"headRefOid": "abc"}', '{"headRefOid": null}']
)
def test_an_answer_without_a_full_sha_is_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str
) -> None:
    _gh(monkeypatch, stdout=stdout)
    assert pr_head.gh_pull_request_head(tmp_path, "7", "o", "r") is None


def test_a_failed_call_is_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gh(monkeypatch, returncode=1, stdout=f'{{"headRefOid": "{SHA}"}}')
    assert pr_head.gh_pull_request_head(tmp_path, "7", "o", "r") is None


def test_a_missing_gh_is_unread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gh(monkeypatch, raises=FileNotFoundError("gh"))
    assert pr_head.gh_pull_request_head(tmp_path, "7", "o", "r") is None


def test_a_missing_repository_is_unread_without_running_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _gh(monkeypatch, stdout=f'{{"headRefOid": "{SHA}"}}')
    assert pr_head.gh_pull_request_head(tmp_path, "7", None, "r") is None
    assert calls == []
