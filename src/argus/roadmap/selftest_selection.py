# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test cases for board selection: claims, refusals, groups and gates."""

import json
import tempfile
from pathlib import Path

from argus.board.reservations import CoordinationError, reservation_file
from argus.roadmap.evidence import needs_decision, signals
from argus.roadmap.queue import gate_block
from argus.roadmap.selftest_fixture import (
    BOARD_REL,
    _EmptyStore,
    _FailingStore,
    _FakeGateStore,
    _FakeStore,
)
from argus.roadmap.server import select_server_phase


def _selection_checks():
    """What the selector offers, waits for, skips, or refuses to claim."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        for number in (100440, 100460, 100601):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text("x\\n")
        fake = _FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(
            repo, "server-01", store=fake, roadmap=repo / "roadmap"
        )
        assert got["state"] == "RESERVED" and got["phase"] == "100440", got
        fake.state["phases"]["100440"]["status"] = "completed"
        (repo / BOARD_REL / "runs" / "phase-100440").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100440" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        fake.state["phases"]["100601"] = {
            "status": "claimed",
            "machine_id": "local-01",
            "reservation_id": "rsv-local",
            "generation": 1,
            "run_id": "phase-100601",
        }
        got = select_server_phase(
            repo, "server-01", store=fake, roadmap=repo / "roadmap"
        )
        assert got["state"] == "RESERVED" and got["phase"] == "100460", got
        fake.state["phases"]["100460"]["status"] = "completed"
        (repo / BOARD_REL / "runs" / "phase-100460").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100460" / "run.json").write_text(
            json.dumps({"state": "completed"})
        )
        got = select_server_phase(
            repo, "server-01", store=fake, reserve=False, roadmap=repo / "roadmap"
        )
        assert got["state"] == "WAIT_FOR_CLAIM" and got["phase"] == "100601", got

        # An operator close-out ticks the approval box but cannot produce a
        # published receipt, so it leaves a live claim behind. A phase that is
        # finished on disk must not gate the line on that stale record: the
        # selector moves past it to the next unfinished phase.
        #
        # The phase carries a rejected run record, because that is what makes
        # the tick count. A tick on a phase nothing ever ran no longer retires
        # it: the box alone used to be enough, and a phase could be marked
        # finished with no run, no ledger and no receipt anywhere.
        (repo / "roadmap" / "phase-100620-synthetic.md").write_text("x\\n")
        (repo / BOARD_REL / "runs" / "phase-100601").mkdir(parents=True, exist_ok=True)
        (repo / BOARD_REL / "runs" / "phase-100601" / "run.json").write_text(
            json.dumps({"state": "rejected"})
        )
        p601 = repo / "roadmap" / "phase-100601-synthetic.md"
        p601.write_text("- [x] Required approval is obtained\n")
        got = select_server_phase(
            repo, "server-01", store=fake, reserve=False, roadmap=repo / "roadmap"
        )
        assert got["state"] == "AVAILABLE" and got["phase"] == "100620", got
        p601.write_text("x\n")

        # The fail-closed half is unchanged: coordination asserting
        # "completed" with no completion evidence anywhere is still an
        # inconsistency, not a silent pass.
        (repo / BOARD_REL / "runs" / "phase-100601").mkdir(parents=True, exist_ok=True)
        fake.state["phases"]["100601"]["status"] = "completed"
        got = select_server_phase(
            repo, "server-01", store=fake, reserve=False, roadmap=repo / "roadmap"
        )
        assert got["state"] == "COORDINATION_INCONSISTENT", got

        try:
            select_server_phase(
                repo, "server-01", store=_FailingStore(), roadmap=repo / "roadmap"
            )
            launch_refused = False
        except CoordinationError:
            launch_refused = True
        assert launch_refused
    with tempfile.TemporaryDirectory() as tmp:
        # A phase that declares a reservation group is selected as one unit:
        # its unfinished remainder is claimed under one shared reservation id
        # and reported as an array. A phase with no declaration still selects
        # alone through the same path, which is the backward-compatible half.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        together = "### Reservation Group\n\n`together: 100010, 100020, 100030`\n"
        for number in (100010, 100020, 100030):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text(together)
        fake = _FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(
            repo, "server-01", store=fake, roadmap=repo / "roadmap"
        )
        assert got["state"] == "RESERVED", got
        assert got["group"] == ["100010", "100020", "100030"], got
        assert [member["phase"] for member in got["members"]] == [
            "100010",
            "100020",
            "100030",
        ], got
        assert len({member["reservation_id"] for member in got["members"]}) == 1, got
        for member in got["members"]:
            token = json.loads(reservation_file(repo, member["phase"]).read_text())
            assert token["reservation_id"] == member["reservation_id"], token
        # The whole group is held now, so the same selection waits for it.
        got = select_server_phase(
            repo, "server-01", store=fake, reserve=False, roadmap=repo / "roadmap"
        )
        assert got["state"] == "WAIT_FOR_CLAIM", got
    with tempfile.TemporaryDirectory() as tmp:
        # A single phase that declares no group is still selected alone and
        # reported without a group array: the single-phase contract is intact.
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        (repo / "roadmap" / "phase-100010-plain.md").write_text("## 1. Objective\n")
        fake = _FakeStore(private=repo / BOARD_REL)
        got = select_server_phase(
            repo, "server-01", store=fake, roadmap=repo / "roadmap"
        )
        assert got["state"] == "RESERVED" and got["phase"] == "100010", got
        assert "members" not in got and "group" not in got, got
    with tempfile.TemporaryDirectory() as tmp:
        # A run that ended rejected or remedy_escalated is neither done nor
        # offerable. The selector reports it under needs_decision and moves on,
        # instead of offering a phase the driver would recover and launch into
        # glide's "already finished" refusal, halting the line on every tick.
        # This is the phase-100878 wedge (issue #56).
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL / "runs").mkdir(parents=True)
        for number, state in (
            (100010, "rejected"),
            (100020, "remedy_escalated"),
            (100030, "completed"),
        ):
            (repo / "roadmap" / f"phase-{number}-synthetic.md").write_text("x\n")
            run_dir = repo / BOARD_REL / "runs" / f"phase-{number:06d}"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"state": state}))

        got = select_server_phase(
            repo,
            "server-01",
            store=_EmptyStore(private=repo / BOARD_REL),
            reserve=False,
            roadmap=repo / "roadmap",
        )
        assert got["state"] == "NO_PHASE", got
        skipped = got.get("needs_decision") or []
        assert [e["phase"] for e in skipped] == ["100010", "100020"], skipped
        assert [e["state"] for e in skipped] == ["rejected", "remedy_escalated"], (
            skipped
        )
        assert needs_decision(repo, 100010) == "rejected"
        assert needs_decision(repo, 100030) is None
        assert (
            signals(
                repo, 100020, repo / "roadmap" / "phase-100020-synthetic.md", set()
            )["needs_decision"]
            is True
        )
    with tempfile.TemporaryDirectory() as tmp:
        # An open decision gate stops the selector before it reserves. This is
        # the 100817 regression: the gate was real, the developer stopped on it
        # mid-run, and a reservation plus a branch were spent getting there.
        # Assert both halves: the open gate is refused, and recording the
        # choice makes the very same phase selectable with no other change.

        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / BOARD_REL).mkdir(parents=True)
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        gate_plan = repo / "roadmap" / "phase-100440-gated.md"
        gate_plan.write_text(
            "## 1. Objective\n"
            "### Sources\n"
            "| Issue | Subject | Kind |\n"
            "|---|---|---|\n"
            "| [#1](x) | something | Security |\n"
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `open` | — | — |\n"
        )
        store = _FakeGateStore(private=repo / BOARD_REL)
        got = select_server_phase(
            repo, "server-01", store=store, roadmap=repo / "roadmap"
        )
        assert got["state"] == "BLOCKED_ON_GATE", got
        assert got["phase"] == "100440", got
        assert store.reserved == [], "an open gate must not reserve a phase"

        # Same file, decision recorded: the phase is selectable, and only the
        # recorded choice is what changed.
        gate_plan.write_text(
            "## 1. Objective\n"
            "### Sources\n"
            "| Issue | Subject | Kind |\n"
            "|---|---|---|\n"
            "| [#1](x) | something | Security |\n"
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `recorded` 2026-09-28 "
            "| Option 1 | Option 2 |\n"
        )
        got = select_server_phase(
            repo, "server-01", store=store, roadmap=repo / "roadmap"
        )
        assert got["state"] == "RESERVED" and got["phase"] == "100440", got
        assert store.reserved == [100440], store.reserved

        # A gate section that claims `recorded` with no choice is a defect in
        # the plan, and the selector fails closed on it rather than launching.
        gate_plan.write_text(
            "### Decision Gates\n"
            "| Gate | Issue | State | Choice | Untaken branch |\n"
            "|---|---|---|---|---|\n"
            "| Which option ships | #1 | `recorded` 2026-09-28 | — | — |\n"
        )
        assert gate_block(100440, gate_plan) is not None, (
            "recorded with an empty choice must fail closed"
        )
