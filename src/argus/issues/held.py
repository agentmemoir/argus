# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Needs You notices for an issue that an Argus pull request holds.

A claimed issue whose Argus pull request closed without merge stays held, and
Argus says so. A ready Argus pull request with a failing check is left to a
person, and Argus says so. Each read that decides a notice fails closed: when
GitHub cannot answer, the tick raises and selects nothing.
"""

from pathlib import Path
from typing import Any

from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.issue_paths import issue_branch
from argus.issues.client import IssueClient
from argus.issues.filter import label_claimed
from argus.issues.task import task_path
from argus.issues.transport import IssuesUnavailable


def _one_line(value: object) -> str:
    """Remote text as one line, so a notice cannot break the line that carries it."""
    return " ".join(str(value).split())


def _closed_notice(number: int, url: str) -> dict[str, str]:
    return {
        "key": f"closed-{number}",
        "text": (
            f"Needs You. Issue #{number} stays held: its Argus pull request {url} "
            "closed without merge. Action: remove the claim label to hand the "
            "issue back."
        ),
    }


def _red_notice(number: int, url: str, sha: str, failed: list[str]) -> dict[str, str]:
    names = ", ".join(failed)
    commit = f" on commit {sha[:12]}" if sha else ""
    return {
        "key": f"red-{number}",
        "text": (
            f"Needs You. Argus pull request {url} is ready for review, and check "
            f"{names} failed{commit}. Action: fix the failing check by hand. Argus "
            "runs no repair and no issue run on a ready pull request."
        ),
    }


def tick_notices(
    client: IssueClient,
    root: Path,
    directory: Path,
    claim_label: str,
    rows: list[dict[str, Any]],
    records: dict[str, Any],
    open_pulls: dict[int, dict[str, Any]],
) -> list[dict[str, str]]:
    """The Needs You notices for one tick, as ``key`` and ``text`` pairs.

    ``open_pulls`` is the open Argus pull requests keyed by issue number. A read
    that cannot answer raises IssuesUnavailable, so the caller selects nothing.
    """
    notices: list[dict[str, str]] = []
    for row in rows:
        number = int(row["number"])
        if number in open_pulls or not label_claimed(
            row, records.get(f"{number:06d}"), claim_label
        ):
            continue
        branch = issue_branch(number, task_path(directory, row).name)
        for pull in client.pull_requests_on(branch):
            if pull["state"] == "CLOSED":
                notices.append(_closed_notice(number, _one_line(pull["url"])))
    owner, _, name = client.repo.partition("/")
    for number, pull in sorted(open_pulls.items()):
        if pull["draft"] is not False:
            continue
        pr_number, url, sha = pull["number"], pull["url"], pull["sha"]
        if not isinstance(pr_number, int) or not isinstance(url, str):
            raise IssuesUnavailable("invalid_response", "pr list")
        if not isinstance(sha, str):
            raise IssuesUnavailable("invalid_response", "pr head")
        checks, _code, detail = gh_pull_request_checks(root, pr_number, owner, name)
        if checks is None:
            raise IssuesUnavailable("checks", detail)
        failed = [
            _one_line(check["name"]) or "unnamed check"
            for check in checks
            if check["bucket"] == "fail"
        ]
        if failed:
            notices.append(_red_notice(number, _one_line(url), sha, failed))
    return notices
