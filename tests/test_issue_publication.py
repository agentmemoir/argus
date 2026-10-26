# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Issue publication fences and private snapshot scope use local Git only."""

import subprocess
from collections.abc import Callable
from typing import Any, cast

import pytest

from argus import conventions
from argus.board.files import reservation_file
from argus.board.reservations import ReservationFenced
from argus.gitsync.git import ConfigError, current_branch, git
from argus.gitsync.issue_modes import mode_issue_branch, mode_issue_checkpoint
from argus.gitsync.issue_paths import issue_branch, issue_scope
from argus.gitsync.issue_runstate import mode_issue_clean
from argus.gitsync.publish_issue import publish_issue
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)


def test_issue_branch_contains_number_and_does_not_stack(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    result = mode_issue_branch(publication.root, 1234567, task)
    assert result["ok"], result
    assert result["branch"] == "issue/1234567-fix-input"
    assert result["base"] == "main"
    assert current_branch(publication.private) == "main"
    assert (
        issue_branch(1234568, "issue-1234568-fix-input.md") == "issue/1234568-fix-input"
    )


def test_in_progress_issue_keeps_its_recorded_long_branch(
    publication: Publication,
) -> None:
    long = "backup-restore-refuses-a-backup-of-a-store-with-a-recorded-e"
    _token, task = publication.claim(1234567, slug=long)
    result = mode_issue_branch(publication.root, 1234567, task)
    assert result["ok"], result
    assert result["branch"] == f"issue/1234567-{long}"
    # Resume and publish read the frozen task path, not the title.
    assert (
        issue_scope(publication.root, 1234567)[1][1]
        == task.relative_to(publication.private).as_posix()
    )
    assert issue_branch(1234567, task) == f"issue/1234567-{long}"


@pytest.mark.parametrize(
    "name",
    [
        "issue-1234568-fix-input.md",
        "issue-1234567-..md",
        "issue-1234567-Fix.md",
        "phase-1234567-fix-input.md",
    ],
)
def test_issue_branch_rejects_unsafe_or_mismatched_task_name(name: str) -> None:
    with pytest.raises(ConfigError):
        issue_branch(1234567, name)


def test_issue_publish_pushes_snapshot_and_code_without_phase_receipt(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    head = publication.commit_code()
    result = publish_issue(
        publication.root,
        1234567,
        pull_request="https://github.com/example/project/pull/41",
    )
    assert result["ok"], result
    assert result["state"] == "AWAITING_REVIEW"
    assert result["issue"] == 1234567 and "phase" not in result
    assert git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1] == head
    run = publication.private / "runs" / "issue-1234567"
    assert not (run / "publication.json").exists()
    assert (
        git(publication.private_remote, "show", f"main:tasks/{task.name}")[1]
        == "FROZEN_TASK_SENTINEL"
    )
    assert git(publication.root, "ls-files", "--", "vault")[1] == ""
    assert "completion_receipt" not in publication.store.read()[0]["phases"]["1234567"]


def test_issue_cleanup_never_commits_another_task_or_run(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    _other, sibling = publication.claim(1234568)
    result = mode_issue_clean(publication.root, 1234567)
    assert result["ok"], result
    assert git(publication.private, "ls-files", "--", f"tasks/{task.name}")[1]
    assert not git(publication.private, "ls-files", "--", f"tasks/{sibling.name}")[1]
    assert not git(publication.private, "ls-files", "--", "runs/issue-1234568")[1]
    assert sibling.exists()


def test_issue_cleanup_sweep_finds_seven_digit_issue_runs(
    publication: Publication,
) -> None:
    publication.claim(1234567)
    publication.claim(1234568)
    result = mode_issue_clean(publication.root)
    assert result["ok"], result
    assert {r["issue"] for r in result["repos"]} == {1234567, 1234568}
    assert git(publication.private, "status", "--porcelain")[1] == ""


def test_issue_cleanup_sweep_skips_a_run_folder_with_no_reservation(
    publication: Publication,
) -> None:
    publication.claim(1234567)
    runs = reservation_file(publication.root, 1234567, kind="issue").parent.parent
    leftover = runs / "issue-1234569"
    leftover.mkdir()
    (leftover / "failed.json").write_text('{"rc": 2}\n')
    result = mode_issue_clean(publication.root)
    assert result["ok"], result
    assert {r["issue"] for r in result["repos"]} == {1234567}
    assert (leftover / "failed.json").exists()


def test_issue_clean_reports_what_its_push_did(publication: Publication) -> None:
    publication.claim(1234567)
    assert mode_issue_clean(publication.root, 1234567)["ok"]
    # Nothing is left to commit and the branch is already pushed: the action
    # names the push's own result, not that a push was attempted.
    second = mode_issue_clean(publication.root, 1234567)
    assert second["ok"], second
    assert second["repos"][0]["action"] == "nothing-to-push"
    assert (
        git(publication.private_remote, "rev-parse", "refs/heads/main")[1]
        == git(publication.private, "rev-parse", "HEAD")[1]
    )


def test_issue_cleanup_reports_unrelated_staged_data(publication: Publication) -> None:
    publication.claim(1234567)
    other = publication.private / "other.txt"
    other.write_text("other\n", encoding="utf-8")
    assert git(publication.private, "add", "other.txt")[0] == 0
    before = git(publication.private, "rev-parse", "HEAD")[1]
    result = mode_issue_clean(publication.root, 1234567)
    assert result["ok"]
    assert result["repos"][0]["staged"] == ["other.txt"]
    assert git(publication.private, "rev-parse", "HEAD")[1] == before


def test_changed_snapshot_refuses_before_any_publication(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    before = git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1]
    publication.commit_code()
    task.write_text("changed task\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="frozen task"):
        publish_issue(publication.root, 1234567)
    assert git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1] == before


def test_public_task_placement_fails_closed(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    conventions.override({"issues.task-dir": publication.root / "public-tasks"})
    before = git(publication.root, "rev-parse", "HEAD")[1]
    with pytest.raises(ConfigError, match="inside the private"):
        mode_issue_branch(publication.root, 1234567, task)
    assert git(publication.root, "rev-parse", "HEAD")[1] == before
    assert not git(publication.root, "diff", "--cached", "--name-only")[1]


def test_lost_owner_blocks_public_code_push(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    before = git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1]
    publication.commit_code()
    saved = cast("Callable[..., subprocess.CompletedProcess[str]]", subprocess.run)
    lost = False

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal lost
        result = saved(argv, **kwargs)
        if (
            argv[:3] == ["git", "-C", str(publication.private)]
            and argv[3] == "push"
            and argv[-2:] == ["origin", "main"]
            and not lost
        ):
            lost = True
            publication.store.release_group(token)
        return result

    monkeypatch.setattr(subprocess, "run", run)
    result = publish_issue(publication.root, 1234567)
    assert lost
    assert not result["ok"] and result["state"] == "FENCED"
    assert git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1] == before


def test_issue_branch_must_name_the_frozen_snapshot(publication: Publication) -> None:
    publication.claim(1234567)
    with pytest.raises(ConfigError, match="differs from the frozen"):
        mode_issue_branch(publication.root, 1234567, "issue-1234567-other.md")
    assert current_branch(publication.root) == "main"


def test_issue_checkpoint_excludes_private_data_without_an_ignore_rule(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    (publication.root / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    (publication.root / "code.txt").write_text("partial fix\n", encoding="utf-8")
    result = mode_issue_checkpoint(publication.root)
    assert result["ok"], result
    assert git(publication.root, "ls-files", "--", "vault")[1] == ""
    assert git(publication.remote, "show", f"{branch}:code.txt")[1] == "partial fix"
    assert task.read_text() == "FROZEN_TASK_SENTINEL\n"


def test_issue_cleanup_reports_a_mismatched_private_upstream(
    publication: Publication,
) -> None:
    publication.claim(1234567)
    before = git(publication.private, "rev-parse", "HEAD")[1]
    assert (
        git(publication.private, "config", "branch.main.merge", "refs/heads/other")[0]
        == 0
    )
    result = mode_issue_clean(publication.root, 1234567)
    assert result["ok"]
    assert git(publication.private, "rev-parse", "HEAD")[1] == before


def test_lost_owner_during_branch_sync_refuses_the_branch_push(
    publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, task = publication.claim(1234567)
    saved = cast("Callable[..., subprocess.CompletedProcess[str]]", subprocess.run)
    lost = False

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal lost
        result = saved(argv, **kwargs)
        if argv[:4] == ["git", "-C", str(publication.root), "fetch"] and not lost:
            lost = True
            publication.store.release_group(token)
        return result

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ReservationFenced):
        mode_issue_branch(publication.root, 1234567, task)
    assert lost
    assert current_branch(publication.root) == "main"
    assert (
        git(
            publication.remote,
            "rev-parse",
            "--verify",
            "refs/heads/issue/1234567-fix-input",
        )[0]
        != 0
    )


def test_new_fence_cannot_publish_a_previous_claim_snapshot(
    publication: Publication,
) -> None:
    token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    before = git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1]
    publication.commit_code()
    current = publication.store.takeover(
        1234567,
        "worker-one",
        confirm=True,
        expected_reservation_id=token["reservation_id"],
        expected_generation=token["generation"],
    )
    with pytest.raises(ConfigError, match="current claim"):
        publish_issue(
            publication.root,
            1234567,
            reservation_id=current["reservation_id"],
            generation=current["generation"],
        )
    assert git(publication.remote, "rev-parse", f"refs/heads/{branch}")[1] == before
