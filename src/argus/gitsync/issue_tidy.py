# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Return a published issue checkout to its integration branch using GitHub evidence."""

import re
from pathlib import Path
from typing import Any, cast

from argus.gitsync.git import (
    FETCH_TIMEOUT_S,
    blocked,
    current_branch,
    dirty_paths,
    git,
    integration_branch,
    resolve_upstream,
    summarize,
)
from argus.gitsync.issue_paths import issue_number
from argus.gitsync.tidy import return_to_integration
from argus.issues.client import IssueClient, mapping, objects
from argus.issues.transport import IssuesUnavailable


def tidy_issue_checkout(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    here = current_branch(root)
    info: dict[str, Any] = {"repo": str(root), "branch": here}
    if here == integration_branch(root):
        return dict(info, ok=True, action="nothing-to-tidy")
    match = re.fullmatch(r"issue/([0-9]+)-[a-z0-9]+(?:-[a-z0-9]+)*", here)
    if match is None:
        return blocked(info, "checkout is not on an issue branch")
    number = issue_number(match.group(1))
    info["issue"] = number
    dirty = dirty_paths(root)
    if dirty:
        return blocked(info, "the working tree has uncommitted changes", dirty=dirty)
    _branch, remote, remote_branch, why = resolve_upstream(root)
    if why or remote is None or remote_branch != here:
        return blocked(info, why or "issue branch upstream does not match its branch")
    rc, _out, _err = git(root, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return blocked(info, "could not fetch the published issue branch")
    remote_ref = f"refs/remotes/{remote}/{remote_branch}"
    carried = git(root, "merge-base", "--is-ancestor", "HEAD", remote_ref)[0] == 0
    try:
        client = IssueClient()
        issue = client.call(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                client.repo,
                "--json",
                "number,state,closedByPullRequestsReferences",
            ]
        )
        issue = mapping(issue, "issue publication evidence")
        if issue.get("number") != number or issue.get("state") not in {
            "OPEN",
            "CLOSED",
        }:
            raise IssuesUnavailable("invalid_response", "issue publication evidence")
        references = objects(
            issue.get("closedByPullRequestsReferences"), "linked pull requests"
        )
        if issue["state"] == "CLOSED" and carried:
            return dict(return_to_integration(root), issue=number, evidence="closed")
        for reference in references:
            repository = mapping(
                reference.get("repository", {}), "linked pull request repository"
            )
            target = repository.get("nameWithOwner")
            if target is None and repository.get("name"):
                owner = repository.get("owner", {})
                if isinstance(owner, dict):
                    login = cast("dict[str, Any]", owner).get("login")
                    target = f"{login}/{repository['name']}"
            if target and target != client.repo:
                continue
            pr = client.call(
                [
                    "pr",
                    "view",
                    str(issue_number(reference.get("number"))),
                    "--repo",
                    client.repo,
                    "--json",
                    "state,headRefName,headRefOid",
                ]
            )
            pr = mapping(pr, "linked pull request evidence")
            if pr.get("state") not in {
                "OPEN",
                "CLOSED",
                "MERGED",
            }:
                raise IssuesUnavailable(
                    "invalid_response", "linked pull request evidence"
                )
            head = pr.get("headRefOid")
            if pr.get("headRefName") != here or pr["state"] == "CLOSED":
                continue
            if (
                not isinstance(head, str)
                or re.fullmatch(r"[0-9a-fA-F]{40,64}", head) is None
            ):
                raise IssuesUnavailable("invalid_response", "linked pull request head")
            # A linked PR must carry this checkout's exact work, including local
            # commits made after an earlier publication. Keep the branch ref.
            if git(root, "merge-base", "--is-ancestor", "HEAD", head)[0] == 0:
                return dict(
                    return_to_integration(root),
                    issue=number,
                    evidence="linked-pull-request",
                    pull_request_state=pr["state"],
                )
        return dict(info, ok=True, action="issue-not-yet-published")
    except IssuesUnavailable as exc:
        return blocked(info, "cannot read GitHub publication evidence", detail=str(exc))


def mode_issue_tidy(root: str | Path) -> dict[str, Any]:
    result = tidy_issue_checkout(root)
    return {
        "ok": result["ok"],
        "mode": "tidy",
        "kind": "issue",
        "repos": [result],
        "summary": summarize([result], "tidy"),
    }
