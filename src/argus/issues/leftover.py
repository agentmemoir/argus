# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Release the leftover claim of an issue that GitHub reports closed."""

import sys
from pathlib import Path
from typing import Any

from argus.board.reservations import ReservationStore
from argus.issues import claims
from argus.issues.client import IssueClient


def release_closed(store: Any, number: int, record: dict[str, Any] | None) -> None:
    """Give back a leftover claim for a closed issue, and say so in one line."""
    if record is not None:
        store.release_group(claims.token(number, record))
    print(f"issue {number} is closed; leftover run released", file=sys.stderr)


def release_if_closed(repo: str | Path, number: int, machine: str | None) -> bool:
    """Release this machine's leftover run when GitHub reports the issue closed.

    Returns False while the issue is open. Raises when GitHub or the board
    cannot answer, so the caller refuses rather than resume unchecked work.
    """
    client = IssueClient()
    if client.view(number)["state"].lower() == "open":
        return False
    store = ReservationStore(Path(repo).resolve(), issues=True)
    state, _ = store.read()
    record = state["phases"].get(f"{number:06d}")
    if record is not None and (
        record.get("machine_id") != machine or not claims.held(record)
    ):
        record = None
    release_closed(store, number, record)
    return True
