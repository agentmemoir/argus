# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue token persistence, release proof, and validated write-back retries."""

import hashlib
import json
import sys
import time
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


def set_aside(repo: Path, number: int) -> bool:
    """Move an earlier run's folder to ``<folder>.prev-<epoch>`` before a new claim.

    glide's ``--fresh`` restart uses the same name. The new claim then starts a
    clean run, so a finished run never stops it, and the old folder stays as
    evidence. False means the move failed, and the claim waits for the next tick.
    """
    folder = reservation_file(repo, number, kind="issue").parent
    if not (folder / "run.json").exists():
        return True
    target = folder.with_name(f"{folder.name}.prev-{int(time.time())}")
    try:
        if target.exists():
            raise FileExistsError(target.name)
        folder.rename(target)
    except OSError as exc:
        print(f"issue {number}: earlier run not set aside: {exc}", file=sys.stderr)
        return False
    print(f"issue {number}: earlier run set aside as {target.name}", file=sys.stderr)
    return True


def carry_forward(repo: Path, number: int) -> bool:
    """Copy the pair into a run folder that a ``--fresh`` archive emptied (#287).

    glide's ``--fresh`` restart moves the whole run folder to
    ``<folder>.prev-<epoch>``, so a pair written just before the move leaves
    with it. The newest archive that holds both files has the pair this folder
    last had. It is copied, never moved, and only into a folder with no
    ``reservation.json``, so every owner check reads the same token as before
    the archive. The snapshot is written first, so a stop in between leaves no
    token without its snapshot.
    """
    target = reservation_file(repo, number, kind="issue")
    folder = target.parent
    if target.exists() or not folder.is_dir():
        return False
    found: list[tuple[int, Path]] = []
    for old in folder.parent.glob(folder.name + ".prev-*"):
        epoch = old.name.rsplit(".prev-", 1)[1]
        names = ("task-snapshot.json", target.name)
        if epoch.isdigit() and all((old / name).is_file() for name in names):
            found.append((int(epoch), old))
    if not found:
        return False
    newest = max(found)[1]
    try:
        for name in ("task-snapshot.json", target.name):
            write(folder / name, (newest / name).read_text(encoding="utf-8"))
    except OSError:
        return False
    return True


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
