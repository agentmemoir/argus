# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Issue tidy checks a checkout's cleanliness and never reads GitHub for closure."""

from argus.gitsync.git import current_branch
from argus.gitsync.issue_modes import mode_issue_branch
from argus.gitsync.issue_tidy import mode_issue_tidy
from argus.gitsync.publish_issue import publish_issue
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)


def test_issue_tidy_reports_dirty_work(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    (publication.root / "uncommitted.txt").write_text("keep me\n", encoding="utf-8")
    result = mode_issue_tidy(publication.root)
    assert result["ok"]
    assert "uncommitted changes" in result["repos"][0]["warning"]
    assert current_branch(publication.root) == branch
    assert (publication.root / "uncommitted.txt").read_text() == "keep me\n"


def test_published_issue_branch_is_left_in_place(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    publication.commit_code()
    assert publish_issue(publication.root, 1234567)["ok"]
    result = mode_issue_tidy(publication.root)
    assert result["ok"], result
    assert result["repos"][0]["action"] == "issue-not-yet-published"
    assert current_branch(publication.root) == branch


def test_unlinked_issue_leaves_checkout_on_its_branch(publication: Publication) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    result = mode_issue_tidy(publication.root)
    assert result["ok"]
    assert current_branch(publication.root) == branch
