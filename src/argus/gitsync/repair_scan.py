# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The failure evidence of a pull request's checks: the runs to read a log from, and the alerts of the checks that have no run."""

import re
from typing import Any

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


def scan_names(rows: list[dict[str, Any]]) -> list[str]:
    """The failing checks that carry no Actions run to read a log from.

    A code-scanning check reports a result rather than a workflow run, so its
    failure is written as an alert on the pull request instead of in a run log.
    """
    return [
        str(row.get("name"))
        for row in rows
        if row.get("bucket") in _FAILING
        and not _RUN_LINK.search(str(row.get("link", "")))
    ]


def scan_report(ref: dict[str, Any]) -> str | None:
    """The pull request's open code-scanning alerts, as the text the task carries.

    This stands in for a run log: each alert names the rule, its severity, and the
    file and line the finding sits on, which is what a repair has to go and fix.
    """
    rows, _why = open_alerts(ref["owner"], ref["repo"], ref["number"], ref["host"])
    lines: list[str] = []
    for row in rows or []:
        alert = normalize_alert(row)
        where = str(alert.get("path") or "no file given")
        line = f":{alert['line']}" if alert.get("line") else ""
        lines.append(
            f"- {alert.get('severity')} {alert.get('rule')} at {where}{line}"
            f" - {alert.get('url')}"
        )
    return "\n".join(lines) if lines else None


__all__ = ["run_ids", "scan_names", "scan_report"]
