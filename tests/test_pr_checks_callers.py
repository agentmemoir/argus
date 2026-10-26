# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every caller of the check read names the repository its pull request is in.

The helper is replaced with a recorder, so each test checks only what the caller
hands it: the owner, repo, and host its pull request URL names. A bare number
names no URL, so the caller hands over a repository only when the checkout's
remote names one; with no remote, gh is not asked at all.
"""

import argparse
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.gitsync import mode_peek_ci, repair_cli

URL = "https://ghe.example.com/example-org/example-repo/pull/7"


@pytest.fixture(autouse=True)
def command_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gh and git command names the callers read before they call gh."""
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


def _recorder(monkeypatch: pytest.MonkeyPatch, module: object) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(
        _root: object,
        _target: object,
        owner: str | None = None,
        repo: str | None = None,
        host: str | None = None,
    ) -> tuple[None, int, str]:
        seen.update(owner=owner, repo=repo, host=host)
        return None, -1, "recorded"

    monkeypatch.setattr(module, "gh_pull_request_checks", fake)
    return seen


def test_peek_ci_passes_the_repository_its_url_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _recorder(monkeypatch, mode_peek_ci)

    mode_peek_ci.mode_peek_ci(tmp_path, pull_request=URL)

    assert seen == {
        "owner": "example-org",
        "repo": "example-repo",
        "host": "ghe.example.com",
    }


def test_peek_ci_with_a_bare_number_and_no_remote_does_not_ask_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _recorder(monkeypatch, mode_peek_ci)

    out = mode_peek_ci.mode_peek_ci(tmp_path, pull_request=7)

    assert seen == {}, seen
    assert out["ok"] is False, out


def test_repair_passes_the_repository_its_url_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _recorder(monkeypatch, repair_cli)
    args = argparse.Namespace(pull_request=URL, run_dir=tmp_path, launched=False)

    code, _out = repair_cli.run(args, now=0.0)

    assert code == 3
    assert seen == {
        "owner": "example-org",
        "repo": "example-repo",
        "host": "ghe.example.com",
    }
