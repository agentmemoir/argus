# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The issue path opens its draft through the same open-pr mode as a phase."""

from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest

from argus.board.reservations import ReservationFenced
from argus.gitsync import issue_modes
from argus.gitsync import mode_open_pr as open_pr_module
from argus.gitsync.git import ConfigError, git
from argus.gitsync.issue_modes import (
    issue_mode,
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
            "before_create": ANY,
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
    """Depends on the fixture's ignore rule for the private vault/ folder.

    The rule keeps vault/ out of the commit, so the checkpoint adds no
    exclude pathspec. The exclude path itself is not covered here.
    """
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    (publication.root / "code.txt").write_text("partial fix\n", encoding="utf-8")
    (publication.private / "scratch.md").write_text("private note\n", encoding="utf-8")
    result = mode_issue_checkpoint(publication.root)
    # A degraded result also sets ok to true, so the warning key is checked too.
    assert result["ok"] and "warning" not in result, result
    subject = git(publication.remote, "log", "-1", "--format=%s", branch)[1]
    assert subject == f"wip: work in progress on {branch}"
    names = git(publication.remote, "show", "--name-only", "--format=", branch)[1]
    assert "code.txt" in names.splitlines()
    assert not any(name.startswith("vault/") for name in names.splitlines())


def test_issue_checkpoint_refuses_a_branch_the_claim_did_not_record(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    other = "issue/1234567-other"
    git(publication.root, "checkout", "-q", "-b", other)
    (publication.root / "code.txt").write_text("partial fix\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="claimed issue branch"):
        mode_issue_checkpoint(publication.root)
    assert git(publication.remote, "branch", "--list", other)[1] == ""


def test_issue_open_pr_refuses_a_branch_the_claim_did_not_record(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    _token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    git(publication.root, "checkout", "-q", "-b", "issue/1234567-other")
    calls = _record_open_pr(monkeypatch)
    with pytest.raises(ConfigError, match="claimed issue branch"):
        mode_issue_open_pr(publication.root)
    assert calls == []


def test_issue_open_pr_refuses_a_claim_lost_after_the_list_read(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    creates: list[tuple[Any, ...]] = []

    def list_then_lose_claim(*_args: Any) -> tuple[list[dict[str, Any]], str]:
        publication.store.release_group(token)
        return [], ""

    def create(*args: Any, **_kwargs: Any) -> tuple[None, str]:
        creates.append(args)
        return None, "create reached"

    monkeypatch.setattr(
        open_pr_module, "gh_pull_requests_for_branch", list_then_lose_claim
    )
    monkeypatch.setattr(open_pr_module, "gh_create_draft_pull_request", create)
    with pytest.raises(ReservationFenced):
        mode_issue_open_pr(publication.root, body_file=task)
    assert creates == []


def test_issue_open_pr_dispatch_fences_a_lost_claim(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    publication.store.release_group(token)
    calls = _record_open_pr(monkeypatch)
    args = Namespace(
        kind="issue",
        mode="open-pr",
        root=str(publication.root),
        title_file=None,
        body_file=None,
        base=None,
        branch=None,
        repo=None,
        phase=None,
    )
    result = issue_mode(args)
    assert result is not None
    assert result["ok"] is False
    assert result["state"] == "FENCED"
    assert calls == []


@pytest.mark.parametrize("flag", ["branch", "repo", "phase"])
def test_issue_open_pr_refuses_a_flag_it_would_ignore(
    publication: Publication, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    _token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    calls = _record_open_pr(monkeypatch)
    args = Namespace(
        kind="issue",
        mode="open-pr",
        root=str(publication.root),
        title_file=None,
        body_file=None,
        base=None,
        branch=None,
        repo=None,
        phase=None,
    )
    setattr(args, flag, "some-value")
    with pytest.raises(
        ConfigError, match=f"--{flag} does not apply to --kind issue --mode open-pr"
    ):
        issue_mode(args)
    assert calls == []
