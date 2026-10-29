# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Board records and claim release for the issue pull requests Argus owns."""

import sys
from pathlib import Path
from typing import Any

from argus.board.core import TERMINAL_STATUSES, CoordinationError
from argus.issues import claims
from argus.issues.client import IssueClient
from argus.issues.evidence import Evidence
from argus.issues.filter import label_names
from argus.issues.held import tick_notices
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
    evidence: Evidence,
) -> list[dict[str, str]]:
    """The tick's pull request work, in order: notices, board records, releases."""
    open_pulls = evidence.open_pull_requests()
    notices = tick_notices(
        client,
        root,
        directory,
        claim_label,
        rows,
        records,
        open_pulls,
        evidence.pull_history(),
    )
    record_pull_requests(store, machine, records, open_pulls)
    release_merged(
        client, store, machine, claim_label, rows, records, evidence.merged_issues()
    )
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
    store: Any,
    machine: str,
    claim_label: str,
    rows: list[dict[str, Any]],
    records: dict[str, Any],
    merged: set[int],
) -> None:
    """An issue with a merged Argus pull request is never selected again.

    This machine releases its own active claim on it once, and the claim label is
    removed. Both steps are idempotent: a later tick finds no claim and no label.
    A failed release or label write is logged, and the next tick tries again.
    """
    for number in sorted(merged):
        key = f"{number:06d}"
        record = records.get(key)
        if (
            record is None
            or not claims.held(record)
            or record.get("machine_id") != machine
        ):
            continue
        try:
            store.release_group(claims.token(number, record))
        except CoordinationError as exc:
            print(f"issue {number}: merged claim not released: {exc}", file=sys.stderr)
            continue
        records.pop(key, None)
        print(f"issue {number}: merged pull request; claim released", file=sys.stderr)
    for row in rows:
        number = int(row["number"])
        wanted = claim_label.casefold()
        if number not in merged or not any(
            name.casefold() == wanted for name in label_names(row)
        ):
            continue
        try:
            client.remove_label(number, claim_label)
        except IssuesUnavailable as exc:
            print(f"issue {number}: claim label not removed: {exc}", file=sys.stderr)
            continue
        print(
            f"issue {number}: merged pull request; claim label removed", file=sys.stderr
        )


def retire_closed(
    store: Any,
    machine: str,
    number: int,
    records: dict[str, Any],
    evidence: Evidence,
) -> None:
    """Free a published record whose Argus pull request closed without merge.

    A person removes the claim label first. The issue then takes a fresh claim on
    a new branch. Only this machine's own published record is removed, and never
    while an open or merged pull request owns the issue.
    """
    key = f"{number:06d}"
    record = records.get(key)
    if (
        record is None
        or record.get("machine_id") != machine
        or record.get("status") not in TERMINAL_STATUSES
        or evidence.owns(number)
        or number not in evidence.closed_issues()
    ):
        return
    try:
        store.retire_retained(claims.token(number, record))
    except CoordinationError as exc:
        print(f"issue {number}: published record not retired: {exc}", file=sys.stderr)
        return
    records.pop(key, None)
    print(
        f"issue {number}: closed pull request; the issue can be claimed again",
        file=sys.stderr,
    )
