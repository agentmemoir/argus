# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Board doubles the phases self-test drives the selector with.

The real store talks to a Git board. These answer from memory instead, so the
selector's own rules -- which phase is offered, who holds it, what a refusal
says -- can be exercised without a remote or a second checkout. ``_FakeStore``
answers plain claims, ``_FakeGateStore`` records what a gate case claimed, and
the read-only doubles answer a query that must not write anything.
"""

from pathlib import Path
from typing import Any

from argus.board.reservations import CoordinationError

# A fixture path only; production paths come from the loaded config.
BOARD_REL = Path("vault") / "cedar-notes"


class _FakeStore:
    def __init__(self, private: Path | None = None) -> None:
        self.state: dict[str, Any] = {"version": 1, "phases": {}}
        self.reserved: list[tuple[int, str]] = []
        # A reservation token is written beside the store's own private
        # checkout, so a double needs to say which one that is. It is
        # resolved after the fixture root exists, below.
        self.private = private

    def read(self) -> tuple[dict[str, Any], str]:
        return self.state, "fake"

    def reserve(self, phase: int, machine_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002 - the fake mirrors the real store's signature
        record: dict[str, Any] = {
            "phase": f"{int(phase):06d}",
            "machine_id": machine_id,
            "reservation_id": f"rsv-{machine_id}",
            "generation": 1,
            "run_id": f"phase-{int(phase):06d}",
        }
        self.state["phases"][f"{int(phase):06d}"] = record
        self.reserved.append((int(phase), machine_id))
        return record

    def reserve_group(
        self,
        phases: list[str],
        machine_id: str,
        **kwargs: Any,  # noqa: ARG002 - the fake mirrors the real store's signature
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for phase in phases:
            key = f"{int(phase):06d}"
            record = {
                "phase": key,
                "machine_id": machine_id,
                "reservation_id": f"rsv-{machine_id}",
                "generation": 1,
                "run_id": f"phase-{key}",
            }
            self.state["phases"][key] = record
            self.reserved.append((int(phase), machine_id))
            records.append(record)
        return records


class _FailingStore(_FakeStore):
    def reserve(self, phase: int, machine_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002 - the fake mirrors the real store's signature
        raise CoordinationError("coordination push failed")


class _EmptyStore:
    def __init__(self, private: Path) -> None:
        self.private = private

    def read(self) -> tuple[dict[str, Any], str]:
        return {"phases": {}}, "fake"


class _FakeGateStore:
    def __init__(self, private: Path | None = None) -> None:
        self.state: dict[str, Any] = {"version": 1, "phases": {}}
        self.reserved: list[int] = []
        self.private = private

    def read(self) -> tuple[dict[str, Any], str]:
        return self.state, "fake"

    def reserve(self, phase: int, machine_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002 - the fake mirrors the real store's signature
        record: dict[str, Any] = {
            "phase": f"{int(phase):06d}",
            "machine_id": machine_id,
            "reservation_id": f"rsv-{machine_id}",
            "generation": 1,
            "run_id": f"phase-{int(phase):06d}",
        }
        self.state["phases"][f"{int(phase):06d}"] = record
        self.reserved.append(int(phase))
        return record


class _ReadOnlyStore:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"version": 1, "phases": {}}

    def read(self) -> tuple[dict[str, Any], str]:
        return self.state, "fake"


__all__ = [
    "_EmptyStore",
    "_FailingStore",
    "_FakeGateStore",
    "_FakeStore",
    "_ReadOnlyStore",
]
