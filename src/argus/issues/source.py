# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue selection: trust, labels, ordering, evidence, then a fenced claim."""

import sys
from collections import Counter
from pathlib import Path
from typing import Any

from argus import conventions
from argus.board.reservations import (
    CoordinationUnavailable,
    ReservationConflict,
    ReservationCooldown,
    ReservationStore,
)
from argus.issues import claims
from argus.issues.client import IssueClient
from argus.issues.evidence import Evidence, escalated, pending, signals
from argus.issues.filter import eligible
from argus.issues.order import PREFERENCES, ordering
from argus.issues.report import _Reports
from argus.issues.task import render, task_directory, task_path, write
from argus.issues.transport import IssuesUnavailable
from argus.issues.trust import Trust
from argus.select.work_source import WorkSource


class IssuesSource(_Reports, WorkSource):
    name = "issues"

    def __init__(self, repo: str | Path, setting: str | Path | None = None) -> None:  # noqa: ARG002 - registry constructor contract
        self.repo = Path(repo).resolve()
        try:
            self.preference = conventions.setting("issues.preference")
            self.groups = conventions.setting("issues.label-groups")
            self.difficulty = conventions.setting("issues.difficulty-order")
            if (
                conventions.setting("board.repo", None) is None
                or conventions.setting("board.issues-file", None) is None
            ):
                raise ValueError(
                    "issue source requires board.file and board.issues-file"
                )
        except ValueError as exc:
            raise IssuesUnavailable("configuration", str(exc)) from exc
        if self.preference not in PREFERENCES:
            raise IssuesUnavailable(
                "configuration",
                "unknown issues.preference; known preferences: "
                + ", ".join(PREFERENCES),
            )
        if self.preference in {"category", "search"} and not conventions.setting(
            f"issues.{self.preference}", None
        ):
            raise IssuesUnavailable(
                "configuration", f"issues.{self.preference} is required"
            )
        self.directory = task_directory(self.repo)
        self.client = IssueClient()
        self.trust = Trust(self.repo, self.client)
        self.skipped: Counter[str] = Counter(
            dict.fromkeys(
                (
                    "not_open",
                    "is_pull_request",
                    "untrusted_author",
                    "unknown_difficulty",
                    "blocked",
                    "claimed",
                    "finished",
                    "merged_but_open",
                    "watching",
                    "escalated",
                ),
                0,
            )
        )
        for name in self.groups:
            self.skipped[f"missing_labels.{name}"] = 0
            self.skipped[f"{name}_ambiguous"] = 0
        self.watching: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] | None = None
        self.details: dict[int, dict[str, Any]] = {}
        self.records: dict[str, Any] = {}
        self.store: Any = None
        self.truncated = False
        self.evidence = Evidence()

    def _load(
        self, *, reserve: bool = False, store: Any = None, listing: bool = True
    ) -> None:
        if self.rows is not None:
            return
        if self.store is None:
            self.store = store or ReservationStore(self.repo, issues=True)
            try:
                state, _ = self.store.read()
            except CoordinationUnavailable:
                if not reserve:
                    raise
                self.store.migrate_board()
                state, _ = self.store.read()
            self.records = state["phases"]
        if not listing:
            return
        rows = self.client.search(self.preference)
        self.truncated = len(rows) == conventions.setting("issues.list-limit", 200)
        filtered = [
            row
            for row in rows
            if eligible(
                row,
                self.trust,
                self.groups,
                self.difficulty,
                self.skipped,
                conventions.setting("issues.category", None),
            )
        ]
        self.rows = ordering(filtered, self.preference, self.difficulty)

    def _detail(self, row: dict[str, Any]) -> dict[str, Any]:
        number = row["number"]
        if number not in self.details:
            detail = self.client.view(number, row)
            # Recheck changing structured labels/state against the search snapshot.
            if not eligible(
                detail,
                self.trust,
                self.groups,
                self.difficulty,
                self.skipped,
                conventions.setting("issues.category", None),
            ):
                detail["evidence"] = "changed"
            else:
                detail["evidence"] = self.evidence.inspect(detail)
                if detail["evidence"]:
                    reason = detail["evidence"]
                    self.skipped[reason] += 1
                    if reason in {"watching", "merged_but_open"}:
                        self.skipped["finished"] += 1
                    if reason == "watching":
                        self.watching.append(
                            {
                                "issue": str(number),
                                "pr_checks_truncated": detail.get(
                                    "pr_checks_truncated", False
                                ),
                            }
                        )
            self.details[number] = detail
        return self.details[number]

    def _cleanup(self, machine: str, reserve: bool) -> dict[str, Any] | None:
        for key, record in list(self.records.items()):
            if record.get("machine_id") != machine or not claims.held(record):
                continue
            number = int(key)
            if pending(record):
                if reserve:
                    try:
                        claims.retry(self.repo, self.store, self.client, number, record)
                    except IssuesUnavailable as exc:
                        return self._base(
                            "WAIT_FOR_CLAIM",
                            record=record,
                            issue=str(number),
                            status="writeback_pending",
                            error=str(exc),
                        )
                    self.records.pop(key, None)
                else:
                    return self._base(
                        "WAIT_FOR_CLAIM",
                        record=record,
                        issue=str(number),
                        status="writeback_pending",
                        error="validated report write-back is pending",
                    )
                continue
        return None

    def select(
        self, machine_id: str | None = None, *, reserve: bool = True, store: Any = None
    ) -> dict[str, Any]:
        machine = machine_id or conventions.setting("machine.id")
        self._load(reserve=reserve, store=store, listing=False)
        refused = self._cleanup(machine, reserve)
        if refused:
            return refused
        if reserve and self.store.expire_stale():
            state, _ = self.store.read()
            self.records = state["phases"]
        self._load()
        attempts = 0
        last: tuple[dict[str, Any], dict[str, Any]] | None = None
        last_state = "WAIT_FOR_CLAIM"
        for row in self.rows or []:
            number = row["number"]
            record = self.records.get(f"{number:06d}")
            if escalated(record):
                self.skipped["escalated"] += 1
                continue
            if record is not None and claims.held(record):
                self.skipped["claimed"] += 1
                if record.get("machine_id") == machine:
                    frozen = claims.snapshot(self.repo, number, record)
                    if frozen is None:
                        return self._base(
                            "COORDINATION_INCONSISTENT",
                            row,
                            record,
                            error="owned issue snapshot is missing or changed",
                        )
                    return self._base(
                        "WAIT_FOR_CLAIM",
                        row,
                        record,
                        path=frozen.relative_to(self.repo).as_posix(),
                    )
                last = row, record
                last_state = "WAIT_FOR_CLAIM"
                continue
            detail = self._detail(row)
            if detail["evidence"]:
                continue
            path = task_path(self.directory, detail)
            if not reserve:
                return self._base("AVAILABLE", detail)
            write(
                path,
                render(detail, self.client, self.trust, self.groups, self.preference),
            )
            attempts += 1
            try:
                reservation = self.store.reserve(
                    number, machine, run_id=f"issue-{number:06d}"
                )
            except (ReservationConflict, ReservationCooldown) as exc:
                self.skipped["claimed"] += 1
                last = detail, exc.record
                last_state = (
                    "COOLDOWN"
                    if isinstance(exc, ReservationCooldown)
                    else "WAIT_FOR_CLAIM"
                )
                if attempts >= conventions.setting("issues.claim-attempts", 3):
                    break
                continue
            claims.save(self.repo, number, reservation, path)
            return self._base("RESERVED", detail, reservation, attempts=attempts)
        if last:
            return self._base(
                last_state,
                *last,
                attempts=attempts,
                error="issue claims are held or claim race was lost",
            )
        return self._base("NO_PHASE")

    def candidates(self) -> list[tuple[int, Path]]:
        self._load()
        survivors: list[tuple[int, Path]] = []
        for row in self.rows or []:
            record = self.records.get(f"{row['number']:06d}")
            if claims.held(record) or escalated(record):
                continue
            detail = self._detail(row)
            if not detail["evidence"]:
                survivors.append((row["number"], task_path(self.directory, detail)))
        return survivors

    def select_next(self) -> tuple[int, Path] | None:
        result = self.select(reserve=False)
        if result["state"] != "AVAILABLE":
            return None
        return int(result["issue"]), self.repo / result["path"]

    def signals(self, item: tuple[int, Path]) -> dict[str, bool]:
        self._load()
        if item[0] not in self.details:
            row = next((r for r in self.rows or [] if r["number"] == item[0]), None)
            self.details[item[0]] = (
                self._detail(row) if row else self.client.view(item[0])
            )
            if row is None:
                self.details[item[0]]["evidence"] = self.evidence.inspect(
                    self.details[item[0]]
                )
        return signals(
            self.details[item[0]],
            self.records.get(f"{item[0]:06d}"),
            conventions.setting("machine.id", None),
        )

    def verbose(self) -> None:
        for number, _path in self.candidates():
            row = self.details[number]
            self._verbose(row, self.signals((number, _path)))

    def audit(self, apply_index: bool = False) -> int:
        if apply_index:
            raise IssuesUnavailable(
                "configuration", "--fix-index is not supported for issues"
            )
        self._load()
        for row in self.rows or []:
            self._detail(row)
        for key, record in self.records.items():
            if escalated(record) or record.get("status") in {
                "claimed",
                "paused",
                "blocked",
            }:
                print(
                    f"issue audit: issue={int(key)} status={'escalated' if escalated(record) else 'claimed-but-idle'}",
                    file=sys.stderr,
                )
        self._base("NO_PHASE")
        return 0
