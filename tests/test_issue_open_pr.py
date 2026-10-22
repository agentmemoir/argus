# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The issue path opens its draft through the same open-pr mode as a phase."""

from pathlib import Path
from typing import Any

import pytest

from argus.board.reservations import ReservationFenced
from argus.gitsync import issue_modes
from argus.gitsync.git import git
from argus.gitsync.issue_modes import (
    mode_issue_branch,
    mode_issue_checkpoint,
    mode_issue_open_pr,
)
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)


def _record_open_pr(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(root: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs, root=root))
        return {"ok": True, "mode": "open-pr", "state": "OPEN"}

    monkeypatch.setattr(issue_modes, "mode_open_pr", fake)
    return calls


def test_issue_open_pr_reuses_the_phase_mode_on_the_owned_branch(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    calls = _record_open_pr(monkeypatch)
    body = tmp_path / "pr-body.md"
    result = mode_issue_open_pr(publication.root, body_file=body)
    assert result["ok"], result
    assert calls == [
        {
            "root": publication.root.resolve(),
            "branch": branch,
            "title_file": None,
            "body_file": body,
            "base": None,
        }
    ]


def test_issue_open_pr_refuses_a_branch_that_is_not_an_issue(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    publication.claim(1234567)
    calls = _record_open_pr(monkeypatch)
    result = mode_issue_open_pr(publication.root)
    assert result["warning"] == "open-pr needs an issue branch"
    assert calls == []


def test_issue_open_pr_refuses_a_lost_claim(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    publication.store.release_group(token)
    calls = _record_open_pr(monkeypatch)
    with pytest.raises(ReservationFenced):
        mode_issue_open_pr(publication.root)
    assert calls == []


def test_issue_checkpoint_commits_with_the_wip_subject_past_an_ignore_rule(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    (publication.root / "code.txt").write_text("partial fix\n", encoding="utf-8")
    result = mode_issue_checkpoint(publication.root)
    assert result["ok"], result
    subject = git(publication.remote, "log", "-1", "--format=%s", branch)[1]
    assert subject == f"wip: work in progress on {branch}"
