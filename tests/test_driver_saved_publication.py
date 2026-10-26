# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
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
