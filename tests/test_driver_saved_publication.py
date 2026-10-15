# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Public ticks settle saved receipts while the launch hold stays in place."""

import json

import pytest

from argus.gitsync.git import git
from driver_publication_fixture import PHASE, DriverPublication
from driver_publication_fixture import (
    driver_publication_fixture as driver_publication_fixture,  # noqa: F401
)


@pytest.mark.parametrize(
    "mode", [(), ("--merge-check-only",)], ids=["tick", "maintenance"]
)
def test_halted_saved_receipt_is_published_without_new_work(
    driver_publication: DriverPublication, mode: tuple[str, ...]
) -> None:
    fixture = driver_publication
    before = fixture.snapshot()
    assert before["receipt"]["state"] == "awaiting_review"
    # A real merge reaches the local remote independently of the gh answer.
    assert git(fixture.root, "push", "origin", "HEAD:main")[0] == 0
    fixture.save_transport(
        state="MERGED", head=git(fixture.root, "rev-parse", "HEAD")[1]
    )
    result = fixture.tick(*mode)
    assert result.returncode == 0, result.stderr
    after = fixture.snapshot()
    assert after["receipt"]["state"] == "published"
    assert after["branch"] == "main"
    assert after["board"] == before["board"]
    assert after["claim"] == before["claim"]
    assert after["halt"] == "manual hold\n"
    assert after["branches"] == before["branches"]
    assert after["tmux"] == []
    assert not (fixture.state / "selection.err").exists()
    assert not (fixture.state / "phase-branch.json").exists()
    committed = git(
        fixture.private, "show", f"origin/main:runs/phase-{PHASE}/publication.json"
    )
    assert committed[0] == 0
    assert json.loads(committed[1])["state"] == "published"


def test_repeated_maintenance_keeps_publication_and_claim_stable(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    assert git(fixture.root, "push", "origin", "HEAD:main")[0] == 0
    fixture.save_transport(
        state="MERGED", head=git(fixture.root, "rev-parse", "HEAD")[1]
    )
    assert fixture.tick("--merge-check-only").returncode == 0
    settled = fixture.snapshot()
    private_head = git(fixture.private, "rev-parse", "HEAD")[1]
    result = fixture.tick("--merge-check-only")
    assert result.returncode == 0, result.stderr
    assert fixture.snapshot() == settled
    assert git(fixture.private, "rev-parse", "HEAD")[1] == private_head


def test_group_ready_retry_runs_behind_halt_and_only_transitions_once(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    fixture.prepare_group()
    before = fixture.snapshot()
    result = fixture.tick("--merge-check-only")
    assert result.returncode == 0, result.stderr
    gates = list(fixture.state.glob("group-gate-*.json"))
    assert len(gates) == 1
    gate = gates[0]
    assert json.loads(gate.read_text())["state"] == "HELD"
    marker = fixture.state / f"group-ready-{PHASE + 1}.json"
    assert not marker.exists()
    fixture.save_transport(bucket="pass")
    result = fixture.tick("--merge-check-only")
    assert result.returncode == 0, result.stderr
    assert json.loads(gate.read_text())["state"] == "READY"
    assert marker.exists()
    marked = marker.read_bytes()
    assert fixture.tick("--merge-check-only").returncode == 0
    assert marker.read_bytes() == marked
    ready = [row for row in fixture.commands() if row[:3] == ["gh", "pr", "ready"]]
    assert len(ready) == 1
    after = fixture.snapshot()
    assert after["board"] == before["board"]
    assert after["claim"] == before["claim"]
    assert after["branch"] == before["branch"]
    assert after["halt"] == "manual hold\n"
    assert after["tmux"] == []


def test_already_ready_group_does_not_freeze_later_ordinary_ticks(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    fixture.prepare_group()
    fixture.save_transport(bucket="pass")
    (fixture.state / "halted").unlink()
    # The transition tick is maintenance-only even without a halt.
    first = fixture.tick()
    assert first.returncode == 0, first.stderr
    assert not (fixture.state / "coordination-sync.json").exists()
    # Existing READY is no new transition: the next idle tick reaches selection.
    second = fixture.tick()
    assert second.returncode == 0, second.stderr
    assert (fixture.state / "coordination-sync.json").exists()
    ready = [row for row in fixture.commands() if row[:3] == ["gh", "pr", "ready"]]
    assert len(ready) == 1


def test_already_published_receipt_retries_checkout_tidy_behind_halt(
    driver_publication: DriverPublication,
) -> None:
    fixture = driver_publication
    assert git(fixture.root, "push", "origin", "HEAD:main")[0] == 0
    fixture.save_transport(
        state="MERGED", head=git(fixture.root, "rev-parse", "HEAD")[1]
    )
    assert fixture.tick("--merge-check-only").returncode == 0
    assert git(fixture.root, "checkout", fixture.branch)[0] == 0
    result = fixture.tick()
    assert result.returncode == 0, result.stderr
    after = fixture.snapshot()
    assert after["branch"] == "main"
    assert after["receipt"]["state"] == "published"
    assert after["halt"] == "manual hold\n"
    assert after["tmux"] == []
