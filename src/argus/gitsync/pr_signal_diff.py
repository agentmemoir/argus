# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What changed between two pull request snapshots, as ordered events."""

from typing import Any

Snapshot = dict[str, Any]


def _by_number(keys: Any) -> list[str]:
    return sorted(keys, key=int)


def diff_snapshots(old: Snapshot | None, new: Snapshot) -> list[dict[str, Any]]:
    """Events that turn ``old`` into ``new``. No snapshot means nothing was seen."""
    before = old or {"alerts": {}, "comments": {}}
    events: list[dict[str, Any]] = []
    for number in _by_number(new["alerts"]):
        alert = new["alerts"][number]
        prior = before["alerts"].get(number)
        if prior is None:
            events.append({"kind": "alert_opened", "number": number, **alert})
        elif prior["state"] != alert["state"]:
            kind, to = "alert_state", alert["state"]
            events.append(
                {"kind": kind, "number": number, "from": prior["state"], "to": to}
            )
    for number in _by_number(before["alerts"].keys() - new["alerts"].keys()):
        events.append({"kind": "alert_removed", "number": number})
    for cid in _by_number(new["comments"]):
        comment = new["comments"][cid]
        prior = before["comments"].get(cid)
        if prior is None:
            events.append({"kind": "comment_added", "id": cid, **comment})
            continue
        if prior["updated_at"] != comment["updated_at"]:
            events.append(
                {
                    "kind": "comment_edited",
                    "id": cid,
                    "updated_at": comment["updated_at"],
                }
            )
        if prior["reactions"] != comment["reactions"]:
            events.append(
                {
                    "kind": "comment_reactions",
                    "id": cid,
                    "from": prior["reactions"],
                    "to": comment["reactions"],
                }
            )
    for cid in _by_number(before["comments"].keys() - new["comments"].keys()):
        events.append({"kind": "comment_removed", "id": cid})
    return events
