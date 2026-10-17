# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase-local reservation and publication files."""

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from argus import conventions
from argus.board.branch import _fetch_branch_ref, _integration_branch
from argus.board.core import (
    PUBLICATION_NAME,
    CoordinationError,
    CoordinationUnavailable,
    _issue_key,
    _phase_key,
    _safe_detail,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
)
from argus.board.git import _git
from argus.conventions import phase_run_dir, runs_dir


def reservation_file(
    repo_root: str | Path,
    phase: str | int,
    *,
    kind: Literal["phase", "issue"] = "phase",
) -> Path:
    """Return the token beside this phase's pipeline run record."""
    if kind == "issue":
        key, _number = _issue_key(phase)
        return runs_dir(repo_root) / f"issue-{key}" / "reservation.json"
    if kind != "phase":
        raise CoordinationError(f"invalid reservation kind: {kind!r}")
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / "reservation.json"


def publication_file(repo_root: str | Path, phase: str | int) -> Path:
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / PUBLICATION_NAME


def _receipt_commit(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise CoordinationError(f"publication receipt has invalid {field}")
    return value.lower()


def _runstate_remote(repo: str | Path, remote: str) -> str:
    """The remote on the run-state checkout to confirm the receipt against.

    The board and the run state may be different repositories, and each names
    its own remote. When the run-state checkout does not define the configured
    board remote, its own first remote is used, so a board-only remote name
    cannot stop the confirmation.
    """
    rc, names, _ = _git(repo, "remote")
    if rc != 0:
        return remote
    configured = [name for name in names.splitlines() if name.strip()]
    if remote in configured:
        return remote
    return configured[0] if configured else remote


def _validate_publication_receipt(
    path: str | Path,
    reservation: Any,
    remote: str,
    _branch: str | None,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(path).resolve()
    key, _number = _phase_key(reservation.get("phase"))
    expected = (
        reservation_file(repo_root or conventions.setting("repo-root"), _number).parent
        / PUBLICATION_NAME
    ).resolve()
    if path != expected:
        raise CoordinationError(
            "publication receipt is outside the phase run directory"
        )
    try:
        raw_data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"publication receipt cannot be read: {exc}") from exc
    if not isinstance(raw_data, dict):
        raise CoordinationError("publication receipt is not a published receipt")
    data = cast("dict[str, Any]", raw_data)
    if data.get("version") != 1 or data.get("state") != "published":
        raise CoordinationError("publication receipt is not a published receipt")
    if data.get("phase") != key:
        raise CoordinationError("publication receipt phase does not match reservation")
    owner_raw = data.get("reservation")
    if not isinstance(owner_raw, dict):
        raise CoordinationError("publication receipt does not match reservation")
    owner = cast("dict[str, Any]", owner_raw)
    if any(
        owner.get(field) != reservation.get(field)
        for field in ("machine_id", "reservation_id", "generation")
    ):
        raise CoordinationError("publication receipt does not match reservation")
    for field in ("public_commit", "private_commit"):
        _receipt_commit(data.get(field), field)
    if not isinstance(data.get("published_at"), str) or not data["published_at"]:
        raise CoordinationError("publication receipt has no publication time")
    # The receipt is run state, published to the integration branch, so it is
    # confirmed against that branch. It used to be confirmed against the board
    # branch, which worked only while the two were the same ref.
    # The receipt is run state, so it is confirmed in the repository that holds
    # the run directory, which need not be the board checkout. The repository
    # is the working tree the run directory sits in.
    rc_top, top, _top_err = _git(expected.parent, "rev-parse", "--show-toplevel")
    if rc_top != 0 or not top:
        raise CoordinationUnavailable(
            "publication receipt is not inside a git working tree"
        )
    run_repo = Path(top).resolve()
    try:
        rel = expected.relative_to(run_repo).as_posix()
    except ValueError as exc:
        raise CoordinationUnavailable(
            "publication receipt is outside the repository that holds the run state"
        ) from exc
    run_remote = _runstate_remote(run_repo, remote)
    published = _integration_branch(run_repo, run_remote)
    ref, _commit = _fetch_branch_ref(run_repo, run_remote, published)
    rc, raw, err = _git(run_repo, "show", f"{ref}:{rel}")
    if rc != 0:
        raise CoordinationUnavailable(
            f"publication receipt is not present on the private remote's "
            f"{published} branch: {_safe_detail(err or raw)}"
        )
    try:
        remote_data = json.loads(raw)
    except ValueError as exc:
        raise CoordinationUnavailable(
            "remote publication receipt does not parse"
        ) from exc
    if remote_data != data:
        raise CoordinationUnavailable("local and remote publication receipts differ")
    return {
        "path": str(path),
        "public_commit": data["public_commit"].lower(),
        "private_commit": data["private_commit"].lower(),
        "published_at": data["published_at"],
    }


def save_reservation_file(path: str | Path, reservation: Any) -> dict[str, Any]:
    """Persist the fence token before a phase process is launched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "issue" if "issue" in reservation else "phase": (
            _issue_key(reservation["issue"])[1]
            if "issue" in reservation
            else reservation["phase"]
        ),
        "machine_id": reservation["machine_id"],
        "reservation_id": reservation["reservation_id"],
        "generation": reservation["generation"],
        "run_id": reservation.get("run_id"),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return payload


def load_reservation_file(path: str | Path) -> dict[str, Any] | None:
    try:
        raw_data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"reservation file is unreadable: {exc}") from exc
    if not isinstance(raw_data, dict):
        raise CoordinationError("reservation file is not an object")
    data = cast("dict[str, Any]", raw_data)
    kind = "issue" if "issue" in data else "phase"
    for field in (kind, "machine_id", "reservation_id", "generation"):
        if field not in data:
            raise CoordinationError(f"reservation file is missing {field}")
    if kind == "issue":
        _issue_key(data["issue"])
    _validate_machine_id(data["machine_id"])
    _validate_reservation_id(data["reservation_id"])
    _validate_generation(data["generation"])
    return data


__all__ = ["_validate_publication_receipt"]
