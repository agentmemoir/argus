# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The failure evidence of a pull request's checks: the runs to read a log from, and the alerts of the checks that have no run."""

import re
from typing import Any

from argus.gitsync.gh_gate import GhRead
from argus.gitsync.pr_signal_fetch import normalize_alert, open_alerts

# A workflow check links to its run, so `gh run view` can open it. A code-scanning
# check links to a scan result, which has no run and therefore no log at all.
_RUN_LINK = re.compile(r"/actions/runs/(\d+)")
_FAILING = ("fail", "cancel")


def run_ids(rows: list[dict[str, Any]]) -> list[int]:
    """The Actions runs of the failing checks, without repeats."""
    return sorted(
        {
            int(m.group(1))
            for row in rows
            if row.get("bucket") in _FAILING
            and (m := _RUN_LINK.search(str(row.get("link", ""))))
        }
    )


def checks_without_run(rows: list[dict[str, Any]]) -> list[str]:
    """The failing checks that carry no workflow run to read a log from.

    A check published by an app rather than by Actions reports its result
    somewhere ``gh run view`` cannot reach. A code-scanning check is the case
    that matters: its failure is written as an alert on the pull request.
    """
    return [
        str(row.get("name"))
        for row in rows
        if row.get("bucket") in _FAILING
        and not _RUN_LINK.search(str(row.get("link", "")))
    ]


def alert_report(ref: dict[str, Any]) -> GhRead[str]:
    """The pull request's open code-scanning alerts as task text, or why not.

    ``value`` is ``None`` when the pull request has no open alert, and ``why``
    says whether the read failed instead. The two must not be read as one
    answer: an unreadable alert list is not an empty one.
    """
    rows, why = open_alerts(ref["owner"], ref["repo"], ref["number"], ref["host"])
    if rows is None:
        return GhRead(None, why)
    lines: list[str] = []
    for row in rows:
        alert = normalize_alert(row)
        where = str(alert.get("path") or "no file given")
        line = f":{alert['line']}" if alert.get("line") else ""
        lines.append(
            f"- {alert.get('severity')} {alert.get('rule')} at {where}{line}"
            f" - {alert.get('url')}"
        )
    return GhRead("\n".join(lines) if lines else None, "")


__all__ = ["alert_report", "checks_without_run", "run_ids"]
