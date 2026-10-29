# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Completion from issue dependencies, Argus pull requests, and board records."""

from typing import Any

from argus.issues.client import IssueClient
from argus.issues.pulls import history


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
    def __init__(self, client: IssueClient) -> None:
        self.client = client
        self.owned = client.argus_pull_requests
        self.held: dict[int, dict[str, Any]] | None = None
        self.past: dict[int, list[dict[str, Any]]] | None = None

    def open_pull_requests(self) -> dict[int, dict[str, Any]]:
        """The open Argus pull requests, read once per tick; a failed read raises."""
        if self.held is None:
            self.held = self.owned()
        return self.held

    def pull_history(self) -> dict[int, list[dict[str, Any]]]:
        """Every Argus pull request in any state, read once per tick; a failure raises."""
        if self.past is None:
            self.past = history(self.client)
        return self.past

    def merged_issues(self) -> set[int]:
        """Issues with a merged Argus pull request, on any suffixed branch."""
        return {
            number
            for number, pulls in self.pull_history().items()
            if any(pull["state"] == "merged" for pull in pulls)
        }

    def closed_issues(self) -> set[int]:
        """Issues with an Argus pull request that closed without merge."""
        return {
            number
            for number, pulls in self.pull_history().items()
            if any(pull["state"] == "closed" for pull in pulls)
        }

    def owns(self, number: int) -> bool:
        """An open or merged Argus pull request keeps its issue out of selection."""
        return number in self.open_pull_requests() or number in self.merged_issues()

    def inspect(self, issue: dict[str, Any]) -> str | None:
        # An open or merged Argus pull request owns its issue; a failed read raises.
        if issue["number"] in self.open_pull_requests():
            return "open_pull_request"
        if issue["number"] in self.merged_issues():
            return "merged_pull_request"
        blockers = issue["blockedBy"]
        # gh fetches 50 nodes. Unread dependencies are conservatively blocked;
        # closed nodes do not block just because they remain linked.
        if blockers["totalCount"] > len(blockers["nodes"]) or any(
            n["state"] == "OPEN" for n in blockers["nodes"]
        ):
            return "blocked"
        return None


def signals(
    _issue: dict[str, Any], record: dict[str, Any] | None, machine_id: str | None
) -> dict[str, bool]:
    record = record or {}
    receipt = record.get("completion_receipt", {})
    return {
        "run": record.get("machine_id") == machine_id
        and record.get("status") in {"claimed", "running", "paused", "blocked"},
        "ledger": receipt.get("writeback_posted") is True,
        "finalize_log": receipt.get("report_completed") is True,
        "approval": bool(record.get("accepted_reason"))
        or receipt.get("cleared") is True,
        "needs_decision": escalated(record),
    }
