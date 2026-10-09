# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phase-local reservation and publication files."""

import json
import re
from pathlib import Path

from argus import conventions
from argus.conventions import phase_run_dir
from argus.phase_reservations_board import _fetch_branch_ref, _integration_branch
from argus.phase_reservations_core import (
    PUBLICATION_NAME,
    CoordinationError,
    CoordinationUnavailable,
    _phase_key,
    _safe_detail,
    _validate_generation,
    _validate_machine_id,
    _validate_reservation_id,
)
from argus.phase_reservations_git import _git


def reservation_file(repo_root, phase):
    """Return the token beside this phase's pipeline run record."""
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / "reservation.json"


def publication_file(repo_root, phase):
    _key, number = _phase_key(phase)
    return phase_run_dir(repo_root, number) / PUBLICATION_NAME


def _receipt_commit(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise CoordinationError(f"publication receipt has invalid {field}")
    return value.lower()


def _validate_publication_receipt(
    path, reservation, private_repo, remote, _branch, *, repo_root=None
):
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
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"publication receipt cannot be read: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("state") != "published"
    ):
        raise CoordinationError("publication receipt is not a published receipt")
    if data.get("phase") != key:
        raise CoordinationError("publication receipt phase does not match reservation")
    owner = data.get("reservation")
    if not isinstance(owner, dict) or any(
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
    published = _integration_branch(private_repo, remote)
    ref, _commit = _fetch_branch_ref(private_repo, remote, published)
    rel = expected.relative_to(Path(private_repo).resolve()).as_posix()
    rc, raw, err = _git(private_repo, "show", f"{ref}:{rel}")
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


def save_reservation_file(path, reservation):
    """Persist the fence token before a phase process is launched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "phase": reservation["phase"],
        "machine_id": reservation["machine_id"],
        "reservation_id": reservation["reservation_id"],
        "generation": reservation["generation"],
        "run_id": reservation.get("run_id"),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return payload


def load_reservation_file(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CoordinationError(f"reservation file is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise CoordinationError("reservation file is not an object")
    for field in ("phase", "machine_id", "reservation_id", "generation"):
        if field not in data:
            raise CoordinationError(f"reservation file is missing {field}")
    _validate_machine_id(data["machine_id"])
    _validate_reservation_id(data["reservation_id"])
    _validate_generation(data["generation"])
    return data
