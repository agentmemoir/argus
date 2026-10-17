# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The run-state repository holds the run directory, not the board.

``driver.runs`` may name a directory inside the workflow checkout while
``board.repo`` names a separate coordination repository. Publishing a phase
still succeeds in that layout, and completion confirms the publication receipt
in the repository that holds the run directory. The completion check used to
look the receipt up in the board checkout, so a run directory outside it raised
a bare ``ValueError`` instead of a coordination error.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationUnavailable
from argus.board.files import PUBLICATION_NAME
from argus.board.reservations import ReservationStore, save_reservation_file
from argus.conventions import board_repo
from argus.gitsync.git import git
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.runstate import runstate_repo
from argus.gitsync.selftest_fixture import _configure_self_test, _init_remote

PHASE = 100760


def _published_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """The receipt as ``mark_phase_published`` rewrites it after a merge."""
    data = dict(receipt)
    data["state"] = "published"
    data["public_commit"] = receipt["public_head"]
    data["private_commit"] = receipt["private_head"]
    return data


def test_completion_confirms_receipt_in_the_decoupled_run_state_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conventions, "_settings", {})
    conventions.configure({"commands.git": "git"})
    _workflow_remote, workflow = _init_remote(tmp_path, "workflow")
    _board_remote, board = _init_remote(tmp_path, "board")
    workflow_runs = workflow / "runs"
    _configure_self_test(workflow, board, runs=workflow_runs)

    # The board and the run state are two different repositories.
    assert board_repo(workflow) == board
    assert runstate_repo(workflow, PHASE) == workflow

    store = ReservationStore(workflow)
    reservation = store.reserve(PHASE, "server-01")
    run_dir = workflow_runs / f"phase-{PHASE:06d}"
    run_dir.mkdir(parents=True)
    save_reservation_file(run_dir / "reservation.json", reservation)
    (run_dir / "run.json").write_text('{"state":"completed"}\n')

    # Phase publish succeeds even though the run state is in the workflow
    # repository and the board is a separate checkout.
    published = publish_phase(workflow, PHASE)
    assert published.get("ok"), published
    assert published.get("state") == "AWAITING_REVIEW", published
    receipt_path = Path(published["receipt"]["path"])
    assert receipt_path == run_dir / PUBLICATION_NAME

    # A published-format receipt the workflow repository's integration branch
    # does not carry is a coordination failure. The receipt check must ask the
    # repository that holds the run directory, and it must never leak the
    # ``ValueError`` a plain ``Path.relative_to`` on the board checkout raised.
    local = _published_receipt(json.loads(receipt_path.read_text()))
    receipt_path.write_text(json.dumps(local, indent=2) + "\n")
    with pytest.raises(CoordinationUnavailable) as raised:
        store.set_status(reservation, "completed", evidence_path=receipt_path)
    assert not isinstance(raised.value, ValueError)

    # Once the receipt is on the integration branch, completion confirms it and
    # the board records the completion receipt.
    assert git(workflow, "add", "-A")[0] == 0
    assert git(workflow, "commit", "-q", "-m", "published receipt")[0] == 0
    assert git(workflow, "push", "-q", "origin", "main")[0] == 0
    done = store.set_status(reservation, "completed", evidence_path=receipt_path)
    assert done.get("status") == "completed", done
    record = store.read()[0]["phases"][reservation["phase"]]
    assert record["status"] == "completed"
    assert record["completion_receipt"]["public_commit"] == (
        local["public_commit"].lower()
    )
    assert record["completion_receipt"]["private_commit"] == (
        local["private_commit"].lower()
    )
