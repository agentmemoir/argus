# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue tidy moves only clean checkouts whose published work GitHub carries."""

from argus.gitsync.git import current_branch, git
from argus.gitsync.issue_modes import mode_issue_branch
from argus.gitsync.issue_tidy import mode_issue_tidy
from argus.gitsync.publish_issue import publish_issue
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)


def test_linked_open_pr_allows_next_issue_launch(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    head = publication.commit_code()
    assert publish_issue(publication.root, 1234567)["ok"]
    publication.github_state(1234567, branch, head)
    result = mode_issue_tidy(publication.root)
    assert result["ok"], result
    assert current_branch(publication.root) == "main"
    assert git(publication.root, "rev-parse", f"refs/heads/{branch}")[1] == head


def test_issue_tidy_refuses_dirty_work(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    (publication.root / "uncommitted.txt").write_text("keep me\n", encoding="utf-8")
    result = mode_issue_tidy(publication.root)
    assert not result["ok"]
    assert current_branch(publication.root) == branch
    assert (publication.root / "uncommitted.txt").read_text() == "keep me\n"


def test_old_pr_does_not_hide_unpushed_commits(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    published = git(publication.root, "rev-parse", "HEAD")[1]
    publication.github_state(1234567, branch, published)
    publication.commit_code()
    result = mode_issue_tidy(publication.root)
    assert result["ok"]
    assert result["repos"][0]["action"] == "issue-not-yet-published"
    assert current_branch(publication.root) == branch


def test_closed_issue_with_published_branch_allows_tidy(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    head = publication.commit_code()
    assert publish_issue(publication.root, 1234567)["ok"]
    publication.github_state(
        1234567, branch, head, issue_state="CLOSED", pr_state="MERGED"
    )
    result = mode_issue_tidy(publication.root)
    assert result["ok"], result
    assert result["repos"][0]["evidence"] == "closed"
    assert current_branch(publication.root) == "main"


def test_merged_pr_allows_tidy_after_remote_branch_deletion(
    publication: Publication,
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    head = publication.commit_code()
    assert publish_issue(publication.root, 1234567)["ok"]
    assert git(publication.remote, "update-ref", "-d", f"refs/heads/{branch}")[0] == 0
    publication.github_state(1234567, branch, head, pr_state="MERGED")
    result = mode_issue_tidy(publication.root)
    assert result["ok"], result
    assert result["repos"][0]["pull_request_state"] == "MERGED"
    assert current_branch(publication.root) == "main"


def test_unlinked_issue_leaves_checkout_on_its_branch(publication: Publication) -> None:
    from issues_fixture import fixture_update, issue

    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    fixture_update(publication.github, rows=[issue(1234567)])
    result = mode_issue_tidy(publication.root)
    assert result["ok"]
    assert current_branch(publication.root) == branch
