# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Board doubles the roadmap self-test drives the selector with.

The real store talks to a Git board. These answer from memory instead, so the
selector's own rules -- which phase is offered, who holds it, what a refusal
says -- can be exercised without a remote or a second checkout. ``_FakeStore``
answers plain claims, ``_FakeGateStore`` records what a gate case claimed, and
the read-only doubles answer a query that must not write anything.
"""

from pathlib import Path

from argus.board.reservations import CoordinationError

# A fixture path only; production paths come from the loaded config.
BOARD_REL = Path("vault") / "cedar-notes"


class _FakeStore:
    def __init__(self, private=None):
        self.state = {"version": 1, "phases": {}}
        self.reserved = []
        # A reservation token is written beside the store's own private
        # checkout, so a double needs to say which one that is. It is
        # resolved after the fixture root exists, below.
        self.private = private

    def read(self):
        return self.state, "fake"

    def reserve(self, phase, machine_id, **kwargs):  # noqa: ARG002 - the fake mirrors the real store's signature
        record = {
            "phase": f"{int(phase):06d}",
            "machine_id": machine_id,
            "reservation_id": f"rsv-{machine_id}",
            "generation": 1,
            "run_id": f"phase-{int(phase):06d}",
        }
        self.state["phases"][f"{int(phase):06d}"] = record
        self.reserved.append((int(phase), machine_id))
        return record

    def reserve_group(self, phases, machine_id, **kwargs):  # noqa: ARG002 - the fake mirrors the real store's signature
        records = []
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
    def reserve(self, phase, machine_id, **kwargs):  # noqa: ARG002 - the fake mirrors the real store's signature
        raise CoordinationError("coordination push failed")


class _EmptyStore:
    def __init__(self, private):
        self.private = private

    def read(self):
        return {"phases": {}}, "fake"


class _FakeGateStore:
    def __init__(self, private=None):
        self.state = {"version": 1, "phases": {}}
        self.reserved = []
        self.private = private

    def read(self):
        return self.state, "fake"

    def reserve(self, phase, machine_id, **kwargs):  # noqa: ARG002 - the fake mirrors the real store's signature
        record = {
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
    def __init__(self):
        self.state = {"version": 1, "phases": {}}

    def read(self):
        return self.state, "fake"
