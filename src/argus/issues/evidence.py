# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Completion from issue dependencies and explicit board records."""

from typing import Any


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
    def inspect(self, issue: dict[str, Any]) -> str | None:
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
