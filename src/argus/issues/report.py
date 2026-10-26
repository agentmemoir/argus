# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selection result shape and safe operational summaries."""

import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from argus import conventions
from argus.issues.client import IssueClient
from argus.issues.evidence import escalated
from argus.issues.filter import label_names
from argus.issues.task import safe_token, stamp, task_path
from argus.issues.trust import Trust


class _Reports:
    repo: Path
    directory: Path
    client: IssueClient
    preference: str
    skipped: Counter[str]
    rows: list[dict[str, Any]] | None
    truncated: bool
    groups: dict[str, Any]
    records: dict[str, Any]
    details: dict[int, dict[str, Any]]
    trust: Trust

    def _verbose(self, row: dict[str, Any], status: dict[str, bool]) -> None:
        allowed = {label for group in self.groups.values() for label in group["labels"]}
        labels = [
            safe_token(label) if label in allowed else "[unrecognised-label]"
            for label in sorted(label_names(row))
        ]
        evidence = " ".join(f"{key}={int(value)}" for key, value in status.items())
        print(
            f"{row['number']}\t{safe_token(row['difficulty'])}\tcreated={stamp(row['createdAt'])} ordering={row['ordering_key']} labels={','.join(labels)} {evidence}",
            file=sys.stderr,
        )

    def _base(
        self,
        state: str,
        row: dict[str, Any] | None = None,
        record: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        result = {
            "state": state,
            "kind": "issue",
            "skipped": dict(self.skipped),
            "preference": self.preference,
            "sort": "best-match" if self.preference == "search" else "created",
            "candidates": [
                {"issue": str(r["number"]), "ordering_key": r["ordering_key"]}
                for r in self.rows or []
                if not self.details.get(r["number"], {}).get("evidence")
                and (
                    self.records.get(f"{r['number']:06d}", {}).get("status")
                    not in {"claimed", "running", "paused", "blocked"}
                    or (
                        row is not None
                        and state == "RESERVED"
                        and r["number"] == row["number"]
                    )
                )
                and not escalated(self.records.get(f"{r['number']:06d}"))
            ],
            "truncated": self.truncated,
            "list_limit": conventions.setting("issues.list-limit", 200),
            "trust": {
                "authors": len(self.trust.authors),
                "apps": len(self.trust.apps),
                "associations": len(self.trust.associations),
                "codeowners": len(self.trust.owners),
                "unresolved": [safe_token(value) for value in self.trust.unresolved],
            },
            "task_dir": {
                "exists": self.directory.is_dir(),
                "writable": os.access(
                    self.directory
                    if self.directory.exists()
                    else self.directory.parent,
                    os.W_OK,
                ),
                "inside_checkout": self.directory.is_relative_to(self.repo),
            },
        }
        if row is not None:
            result.update(
                issue=str(row["number"]),
                path=task_path(self.directory, row).relative_to(self.repo).as_posix(),
            )
        if record:
            result.update(
                {
                    key: record[key]
                    for key in (
                        "machine_id",
                        "status",
                        "reservation_id",
                        "generation",
                        "run_id",
                    )
                    if key in record
                }
            )
            result["owner"] = record.get("machine_id")
        result.update(extra)
        chosen = result.get("issue", "none")
        print(
            f"issue selection: preference={self.preference} repo={self.client.repo} candidates={len(self.rows or [])} chosen={chosen} sort={result['sort']}",
            file=sys.stderr,
        )
        print(
            "issue skips: "
            + " ".join(f"{key}={value}" for key, value in sorted(self.skipped.items())),
            file=sys.stderr,
        )
        if self.truncated:
            print(
                "issue warning: list may be incomplete; raise issues.list-limit",
                file=sys.stderr,
            )
        return result


__all__ = ["_Reports"]
