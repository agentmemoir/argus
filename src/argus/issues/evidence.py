# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Completion from GitHub facts and explicit board records."""

from typing import Any

from argus.issues.client import IssueClient


def escalated(record: dict[str, Any] | None) -> bool:
    receipt = (record or {}).get("completion_receipt", {})
    return (
        receipt.get("state_reason") == "remedy_escalated"
        and receipt.get("cleared") is not True
    )


def pending(record: dict[str, Any] | None) -> bool:
    return (record or {}).get("completion_receipt", {}).get(
        "state_reason"
    ) == "writeback_pending"


class Evidence:
    def __init__(self, client: IssueClient, maximum: int) -> None:
        self.client = client
        self.maximum = maximum
        self.states: dict[str, str] = {}
        self.calls = 0

    def inspect(self, issue: dict[str, Any]) -> str | None:
        if issue["state"].lower() == "closed":
            return "finished"
        blockers = issue["blockedBy"]
        # gh fetches 50 nodes. Unread dependencies are conservatively blocked;
        # closed nodes do not block just because they remain linked.
        if blockers["totalCount"] > len(blockers["nodes"]) or any(
            n["state"] == "OPEN" for n in blockers["nodes"]
        ):
            return "blocked"
        result = None
        for reference in issue["closedByPullRequestsReferences"]:
            key = str(reference.get("repository")) + ":" + str(reference.get("number"))
            if key not in self.states:
                if self.calls >= self.maximum:
                    issue["pr_checks_truncated"] = True
                    return "watching"
                self.states[key] = self.client.pr_state(reference)
                self.calls += 1
            state = self.states[key]
            if state == "MERGED":
                result = "merged_but_open"
            elif state == "OPEN" and result is None:
                result = "watching"
        return result


def signals(
    issue: dict[str, Any], record: dict[str, Any] | None, machine_id: str | None
) -> dict[str, bool]:
    record = record or {}
    receipt = record.get("completion_receipt", {})
    return {
        "run": record.get("machine_id") == machine_id
        and record.get("status") in {"claimed", "running", "paused", "blocked"},
        "ledger": receipt.get("writeback_posted") is True,
        "finalize_log": receipt.get("report_completed") is True,
        "publication": issue.get("evidence") == "merged_but_open",
        "approval": bool(record.get("accepted_reason"))
        or receipt.get("cleared") is True,
        "index": issue["state"].lower() == "closed",
        "needs_decision": escalated(record),
    }
