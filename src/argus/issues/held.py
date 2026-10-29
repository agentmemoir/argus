# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Needs You notices for an issue that an Argus pull request holds.

A claimed issue whose Argus pull request closed without merge stays held, and
Argus says so. A claim that completed or expired with no pull request and keeps
its label says so too, with the one action that hands the issue back. A ready Argus pull request with a failing check is left to a
person, and Argus says so. Each read that decides a notice fails closed: when
GitHub cannot answer, the tick raises and selects nothing.
"""

from pathlib import Path
from typing import Any

from argus.board.core import EXPIRED_STATUS
from argus.gitsync.gh_checks import gh_pull_request_checks
from argus.gitsync.issue_paths import issue_branch
from argus.issues.client import IssueClient
from argus.issues.filter import label_claimed
from argus.issues.task import task_path
from argus.issues.transport import IssuesUnavailable


def _one_line(value: object) -> str:
    """Remote text as one line, so a notice cannot break the line that carries it."""
    return " ".join(str(value).split())


def _closed_notice(number: int, urls: list[str]) -> dict[str, str]:
    """One line for every closed pull request, so the key keeps one text per state."""
    named = (
        f"pull request {urls[0]}"
        if len(urls) == 1
        else "pull requests " + ", ".join(urls)
    )
    return {
        "key": f"closed-{number}",
        "text": (
            f"Needs You. Issue #{number} stays held: its Argus {named} "
            "closed without merge. Action: remove the claim label to hand the "
            "issue back."
        ),
    }


def _no_pull_notice(number: int, status: object) -> dict[str, str] | None:
    """A claim that ended with no pull request, held by its label alone."""
    if status == "completed":
        # The same key and text as the driver's completed-run line, so one is sent.
        reason = (
            "finished its run with no pull request. Argus starts no new run for it."
        )
    elif status == EXPIRED_STATUS:
        reason = (
            "stays held by its claim label: its claim expired with no pull request."
        )
    else:
        return None
    return {
        "key": f"{status}-{number}",
        "text": (
            f"Needs You. Issue #{number} {reason} "
            "Action: remove the claim label to retry."
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
    past: dict[int, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    """The Needs You notices for one tick, as ``key`` and ``text`` pairs.

    ``open_pulls`` is the open Argus pull requests keyed by issue number. A read
    that cannot answer raises IssuesUnavailable, so the caller selects nothing.
    """
    notices: list[dict[str, str]] = []
    for row in rows:
        number = int(row["number"])
        record = records.get(f"{number:06d}")
        if number in open_pulls or not label_claimed(row, record, claim_label):
            continue
        branch = issue_branch(number, task_path(directory, row).name)
        on_branch = client.pull_requests_on(branch)
        urls = {str(p["url"]) for p in on_branch if p["state"] == "CLOSED"}
        urls |= {str(p["url"]) for p in past.get(number, []) if p["state"] == "closed"}
        if urls:
            notices.append(_closed_notice(number, sorted(map(_one_line, urls))))
        elif not on_branch and not past.get(number):
            notice = _no_pull_notice(number, record and record.get("status"))
            if notice:
                notices.append(notice)
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
