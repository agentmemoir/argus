# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read a pull request's code-scanning alerts and review comments as one snapshot.

A read is all or nothing. A failed or malformed read returns no snapshot and a
reason, so a caller that keeps its last good snapshot never reads an unanswered
call as "every alert closed" or "every comment deleted".
"""

import json
from collections.abc import Callable
from typing import Any, cast

from argus.gitsync.gh_gate import _gh
from argus.gitsync.pr_signal_trust import Visibility

Row = dict[str, Any]
Snapshot = dict[str, Any]
Fetch = Callable[[str, str, int, str | None, Visibility], tuple[Snapshot | None, str]]


def _api_rows(path: str, host: str | None, key: str) -> tuple[list[Row] | None, str]:
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
        data: Any = json.loads(done.stdout)
    except ValueError:
        return None, f"gh api printed no JSON reading {key}"
    if not isinstance(data, list):
        return None, f"gh api printed an unexpected shape reading {key}"
    rows: list[Row] = []
    for page in cast("list[object]", data):
        if not isinstance(page, list):
            return None, f"gh api printed an unexpected page reading {key}"
        for raw in cast("list[object]", page):
            if not isinstance(raw, dict):
                return None, f"a row without {key} came back"
            row = cast("Row", raw)
            if row.get(key) is None:
                return None, f"a row without {key} came back"
            rows.append(row)
    return rows, ""


def normalize_alert(row: Row) -> Row:
    """The fields of one code-scanning alert that the watch compares."""
    rule: Row = row.get("rule") or {}
    instance: Row = row.get("most_recent_instance") or {}
    location: Row = instance.get("location") or {}
    return {
        "state": row.get("state"),
        "rule": rule.get("id"),
        "severity": rule.get("security_severity_level") or rule.get("severity"),
        "path": location.get("path"),
        "line": location.get("start_line"),
        "url": row.get("html_url"),
    }


def normalize_comment(row: Row, trusted: bool, show_untrusted: bool) -> Row:
    """The fields of one review comment that the watch compares.

    The text is kept only for a trusted author, or for any author when untrusted
    text is shown as data. Otherwise the record has no text at all.
    """
    user: Row = row.get("user") or {}
    reactions: Row = row.get("reactions") or {}
    return {
        "author": user.get("login"),
        "path": row.get("path"),
        "line": row.get("line"),
        "in_reply_to": row.get("in_reply_to_id"),
        "updated_at": row.get("updated_at"),
        "reactions": reactions.get("total_count", 0),
        "trusted": trusted,
        "body": (row.get("body") or "") if trusted or show_untrusted else None,
    }


def fetch_snapshot(
    owner: str,
    repo: str,
    number: int,
    host: str | None = None,
    visibility: Visibility | None = None,
) -> tuple[Snapshot | None, str]:
    """The pull request's alerts and review comments, or None and why not.

    Alerts are read with ``state=all`` so a fixed or dismissed alert stays visible
    as a state change, not as a row that vanished. Without a visibility, no
    comment text is kept.
    """
    view = visibility or Visibility.closed()
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
    comment_records: dict[str, Row] = {}
    for row in comments:
        trusted = view.trusted(row)
        comment_records[str(row["id"])] = normalize_comment(
            row, trusted, view.show_untrusted
        )
    snapshot: Snapshot = {
        "alerts": {str(row["number"]): normalize_alert(row) for row in alerts},
        "comments": comment_records,
    }
    return snapshot, ""
