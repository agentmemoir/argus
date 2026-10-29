# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Board records and label release for the issue pull requests Argus owns."""

import sys
from pathlib import Path
from typing import Any

from argus.board.core import TERMINAL_STATUSES, CoordinationError
from argus.gitsync.issue_paths import issue_branch
from argus.issues import claims
from argus.issues.client import IssueClient
from argus.issues.filter import label_claimed
from argus.issues.held import tick_notices
from argus.issues.task import task_path
from argus.issues.transport import IssuesUnavailable


def tick_pull_requests(
    client: IssueClient,
    store: Any,
    machine: str,
    root: Path,
    directory: Path,
    claim_label: str,
    rows: list[dict[str, Any]],
    records: dict[str, Any],
    open_pulls: dict[int, dict[str, Any]],
) -> list[dict[str, str]]:
    """The tick's pull request work, in order: notices, board records, labels."""
    notices = tick_notices(
        client, root, directory, claim_label, rows, records, open_pulls
    )
    record_pull_requests(store, machine, records, open_pulls)
    release_merged(client, directory, claim_label, rows, records, open_pulls)
    return notices


def record_pull_requests(
    store: Any,
    machine: str,
    records: dict[str, Any],
    open_pulls: dict[int, dict[str, Any]],
) -> None:
    """Put each open Argus pull request on its issue's board record, once.

    Only the machine that owns a record may write it, so another machine's record
    is recorded on that machine's own tick.
    """
    for number, pull in sorted(open_pulls.items()):
        record = records.get(f"{number:06d}")
        if record is None or record.get("machine_id") != machine:
            continue
        wanted = {"branch": pull["branch"], "url": pull["url"]}
        if record.get("pull_request") == wanted:
            continue
        try:
            store.set_issue_pull_request(claims.token(number, record), wanted)
        except CoordinationError as exc:
            print(
                f"issue {number}: pull request not recorded on the board: {exc}",
                file=sys.stderr,
            )
            continue
        record["pull_request"] = wanted


def release_merged(
    client: IssueClient,
    directory: Path,
    claim_label: str,
    rows: list[dict[str, Any]],
    records: dict[str, Any],
    open_pulls: dict[int, dict[str, Any]],
) -> None:
    """Remove the claim label from an issue whose Argus pull request merged.

    Only an issue whose board record is already retained as published (terminal)
    is handled. The board refuses a new claim on such a record, so the label can
    go without starting the issue again. An active claim is left to its run, and
    a missing or expired record keeps its label, because removing that label
    could let the issue run again. A failed read or label write changes nothing,
    and the next tick tries again.
    """
    for row in rows:
        number = int(row["number"])
        record = records.get(f"{number:06d}")
        if (
            number in open_pulls
            or record is None
            or record.get("status") not in TERMINAL_STATUSES
            or not label_claimed(row, record, claim_label)
        ):
            continue
        branch = issue_branch(number, task_path(directory, row).name)
        try:
            pulls = client.pull_requests_on(branch)
            if not any(pull["state"] == "MERGED" for pull in pulls):
                continue
            client.remove_label(number, claim_label)
        except IssuesUnavailable as exc:
            print(
                f"issue {number}: merge check or label removal failed; label kept: {exc}",
                file=sys.stderr,
            )
            continue
        print(
            f"issue {number}: pull request merged; claim label removed", file=sys.stderr
        )
