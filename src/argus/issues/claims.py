# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue token persistence, release proof, and validated write-back retries."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from argus.board.reservations import (
    ACTIVE_STATUSES,
    CoordinationError,
    reservation_file,
    save_reservation_file,
)
from argus.issues.client import IssueClient
from argus.issues.evidence import pending
from argus.issues.task import write
from argus.issues.transport import IssuesUnavailable


def token(number: int, record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "issue": str(number)}


def held(record: dict[str, Any] | None) -> bool:
    return record is not None and record.get("status") in ACTIVE_STATUSES


def save(repo: Path, number: int, reservation: dict[str, Any], path: Path) -> None:
    try:
        target = reservation_file(repo, number, kind="issue")
        save_reservation_file(target, reservation)
        manifest = {
            "reservation_id": reservation["reservation_id"],
            "generation": reservation["generation"],
            "path": path.relative_to(repo).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        write(target.with_name("task-snapshot.json"), json.dumps(manifest))
    except (OSError, ValueError) as exc:
        raise CoordinationError("could not persist issue reservation token") from exc


def snapshot(repo: Path, number: int, record: dict[str, Any]) -> Path | None:
    try:
        target = reservation_file(repo, number, kind="issue").with_name(
            "task-snapshot.json"
        )
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        manifest = cast("dict[str, Any]", raw)
        if (
            manifest.get("reservation_id") != record["reservation_id"]
            or manifest.get("generation") != record["generation"]
        ):
            return None
        path = (repo / manifest["path"]).resolve()
        if (
            not path.is_relative_to(repo)
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]
        ):
            return None
        return path
    except (OSError, ValueError, KeyError, TypeError):
        return None


def payload(repo: Path, receipt: dict[str, Any]) -> Path:
    value = receipt.get("body_file")
    if not isinstance(value, str) or Path(value).is_absolute():
        raise IssuesUnavailable(
            "writeback_payload", "body_file must be checkout relative"
        )
    path = (repo / value).resolve()
    if not path.is_relative_to(repo.resolve()) or receipt.get("validated") is not True:
        raise IssuesUnavailable("writeback_payload", "report payload needs validation")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise IssuesUnavailable(
            "writeback_payload", "report payload cannot be read"
        ) from exc
    if digest != receipt.get("sha256"):
        raise IssuesUnavailable(
            "writeback_payload", "report payload changed after validation"
        )
    return path


def retry(
    repo: Path, store: Any, client: IssueClient, number: int, record: dict[str, Any]
) -> None:
    if not pending(record):
        return
    reservation = token(number, record)
    store.assert_owner(reservation)
    path = payload(repo, record["completion_receipt"])
    client.comment(
        number,
        path,
        report=True,
        expected_sha256=record["completion_receipt"]["sha256"],
    )
    store.set_issue_receipt(reservation, None)
    store.release_group(reservation)
