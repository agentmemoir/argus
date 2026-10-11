# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test fixtures: the check recorder, a test repo, a receipt."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.board.core import PUBLICATION_NAME, _now
from argus.board.git import _git, _head, git_binary
from argus.board.state import _write_state_file, state_rel
from argus.board.store import ReservationStore


def _self_test_check(
    checks: list[dict[str, Any]],
    name: str,
    ok: object,
    detail: str = "",
) -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _git_test_repo(
    base: Path,
    name: str,
    initial_state: dict[str, Any] | None = None,
) -> tuple[Path, list[Path]]:
    """Create one local bare remote and one client checkout for self-tests."""
    remote = base / f"{name}-remote.git"
    subprocess.run(
        [git_binary(), "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/master")
    seed = base / f"{name}-seed"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(seed)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(seed, "config", "user.email", "coordination-test@example.com")
    _git(seed, "config", "user.name", "coordination test")
    _git(seed, "checkout", "-q", "-b", "master")
    (seed / "seed.txt").write_text("seed\n")
    if initial_state is not None:
        _write_state_file(seed / state_rel(), initial_state)
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "coordination seed")
    _git(seed, "push", "-q", "-u", "origin", "master")
    clients: list[Path] = []
    for index in (1, 2):
        client = base / f"{name}-client-{index}"
        subprocess.run(
            [git_binary(), "clone", "-q", str(remote), str(client)],
            check=True,
            capture_output=True,
            text=True,
        )
        _git(client, "config", "user.email", "coordination-test@example.com")
        _git(client, "config", "user.name", "coordination test")
        clients.append(client)
    return remote, clients


def _self_test_receipt(store: ReservationStore, reservation: dict[str, Any]) -> Path:
    _git(store.private, "fetch", "origin", "master")
    _git(store.private, "merge", "--ff-only", "origin/master")
    path = store.private / "runs" / f"phase-{reservation['phase']}" / PUBLICATION_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 1,
        "phase": reservation["phase"],
        "state": "published",
        "reservation": {
            "machine_id": reservation["machine_id"],
            "reservation_id": reservation["reservation_id"],
            "generation": reservation["generation"],
        },
        "public_commit": "0" * 40,
        "private_commit": _head(store.private),
        "published_at": _now(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    _git(store.private, "add", "--", path.relative_to(store.private).as_posix())
    _git(store.private, "commit", "-q", "-m", "test publication receipt")
    _git(store.private, "push", "origin", "HEAD:master")
    return path


__all__ = ["_git_test_repo", "_self_test_check", "_self_test_receipt"]
