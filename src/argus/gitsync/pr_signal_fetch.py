# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read a pull request's code-scanning alerts and review comments as one snapshot.

A read is all or nothing. A failed or malformed read returns no snapshot and a
reason, so a caller that keeps its last good snapshot never reads an unanswered
call as "every alert closed" or "every comment deleted".
"""

import json
from collections.abc import Callable
from typing import Any

from argus.gitsync.gh_gate import _gh

Snapshot = dict[str, Any]
Fetch = Callable[[str, str, int, str | None], tuple[Snapshot | None, str]]


def _api_rows(path: str, host: str | None, key: str) -> tuple[list[Any] | None, str]:
    """Every row of a paginated list endpoint, or None and why not."""
    argv = ["api", "--paginate", "--slurp", path]
    if host and host != "github.com":
        argv = ["api", "--hostname", host, "--paginate", "--slurp", path]
    done, why = _gh(argv)
    if done is None:
        return None, why
    if done.returncode != 0:
        return None, f"gh api exited {done.returncode} reading {key}"
    try:
        pages: object = json.loads(done.stdout)
    except ValueError:
        return None, f"gh api printed no JSON reading {key}"
    if not isinstance(pages, list):
        return None, f"gh api printed an unexpected shape reading {key}"
    rows: list[Any] = []
    for page in pages:
        if not isinstance(page, list):
            return None, f"gh api printed an unexpected page reading {key}"
        rows.extend(page)
    if not all(isinstance(row, dict) and row.get(key) is not None for row in rows):
        return None, f"a row without {key} came back"
    return rows, ""


def _alert(row: dict[str, Any]) -> dict[str, Any]:
    rule = row.get("rule") or {}
    location = (row.get("most_recent_instance") or {}).get("location") or {}
    return {
        "state": row.get("state"),
        "rule": rule.get("id"),
        "severity": rule.get("security_severity_level") or rule.get("severity"),
        "path": location.get("path"),
        "line": location.get("start_line"),
        "url": row.get("html_url"),
    }


def _comment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "author": (row.get("user") or {}).get("login"),
        "path": row.get("path"),
        "line": row.get("line"),
        "in_reply_to": row.get("in_reply_to_id"),
        "updated_at": row.get("updated_at"),
        "reactions": (row.get("reactions") or {}).get("total_count", 0),
        "body": row.get("body") or "",
    }


def fetch_snapshot(
    owner: str, repo: str, number: int, host: str | None = None
) -> tuple[Snapshot | None, str]:
    """The pull request's alerts and review comments, or None and why not.

    Alerts are read with ``state=all`` so a fixed or dismissed alert stays visible
    as a state change, not as a row that vanished.
    """
    alerts, why = _api_rows(
        f"repos/{owner}/{repo}/code-scanning/alerts"
        f"?state=all&ref=refs/pull/{number}/head&per_page=100",
        host,
        "number",
    )
    if alerts is None:
        return None, why
    comments, why = _api_rows(
        f"repos/{owner}/{repo}/pulls/{number}/comments?per_page=100", host, "id"
    )
    if comments is None:
        return None, why
    snapshot: Snapshot = {
        "alerts": {str(row["number"]): _alert(row) for row in alerts},
        "comments": {str(row["id"]): _comment(row) for row in comments},
    }
    return snapshot, ""
