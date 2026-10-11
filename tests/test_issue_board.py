# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Issue namespaces share Git transactions without sharing phase records."""

import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board import worktree
from argus.board.core import (
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationFenced,
    _issue_key,
)
from argus.board.files import (
    load_reservation_file,
    reservation_file,
    save_reservation_file,
)
from argus.board.git import _git
from argus.board.selftest_fixture import _git_test_repo
from argus.board.state import _validate_state, state_rel
from argus.board.store import ReservationStore
from config_fixture import BASE, write


@pytest.fixture
def board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ReservationStore, ReservationStore, ReservationStore]:
    project = tmp_path / "project"
    project.mkdir()
    executable = shutil.which("git")
    assert executable is not None
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": project,
            "board.repo": tmp_path / "claims-client-1",
            "board.branch": "fixture-claims",
            "board.remote": "origin",
            "board.file": "coordination/phases.json",
            "board.issues-file": "coordination/issues.json",
            "commands.git": executable,
            "driver.runs": "runs",
        },
    )
    _remote, clients = _git_test_repo(tmp_path, "claims")
    phases = ReservationStore(project)
    issues = ReservationStore(project, issues=True)
    rival = ReservationStore(project, private_repo=clients[1], issues=True)
    phases.migrate_board()
    return phases, issues, rival


@pytest.mark.parametrize(
    ("value", "key", "number"),
    [
        (1, "000001", 1),
        ("000042", "000042", 42),
        (123456, "123456", 123456),
        (1234567, "1234567", 1234567),
        (9999999999, "9999999999", 9999999999),
    ],
)
def test_issue_key_round_trips(value: str | int, key: str, number: int) -> None:
    assert _issue_key(value) == (key, number)
    assert _issue_key(key) == (key, number)


@pytest.mark.parametrize(
    "value", [0, -1, 10000000000, True, False, None, 1.5, "one", "1.0", "+1", " 1"]
)
def test_issue_key_rejects_invalid_numbers(value: object) -> None:
    with pytest.raises(CoordinationError):
        _issue_key(value)


def test_issue_keys_are_validated_in_their_own_namespace() -> None:
    record = {
        "status": "claimed",
        "machine_id": "machine-1",
        "reservation_id": "rsv-1",
        "generation": 1,
    }
    state = {"version": 1, "phases": {"1234567": record}}
    assert _validate_state(state, issues=True) == state
    with pytest.raises(CoordinationUnavailable, match="invalid phase key"):
        _validate_state(state)
    for key in ("0", "000000", "1", "0000001"):
        with pytest.raises(CoordinationUnavailable, match="issue"):
            _validate_state({"version": 1, "phases": {key: record}}, issues=True)


def test_phase_and_issue_claims_do_not_share_records(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    phases, issues, rival = board
    assert issues.read()[0] == {"version": 1, "phases": {}}
    phase = phases.reserve(42, "machine-1")
    issue = issues.reserve(42, "machine-2")
    assert phase["phase"] == "000042"
    assert issue["issue"] == 42
    assert issue["run_id"] == "issue-000042"
    assert "phase" not in issue
    assert phases.read()[0]["phases"]["000042"]["machine_id"] == "machine-1"
    assert rival.read()[0]["phases"]["000042"]["machine_id"] == "machine-2"
    assert issues.renew(issue, "running")["status"] == "running"
    assert issues.release_group(issue) == ["000042"]
    assert issues.read()[0]["phases"] == {}
    assert phases.assert_owner(phase)["status"] == "claimed"


def test_push_retry_keeps_a_concurrent_phase_write(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases, issues, _rival = board
    original = _git
    intercepted = False

    def race(repo: str | Path, *args: str, **kwargs: Any) -> tuple[int, str, str]:
        nonlocal intercepted
        if args[0] == "push" and not intercepted:
            intercepted = True
            phases.reserve(42, "machine-phase")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", race)
    assert issues.reserve(42, "machine-issue")["issue"] == 42
    assert intercepted
    assert phases.read()[0]["phases"]["000042"]["machine_id"] == "machine-phase"
    assert issues.read()[0]["phases"]["000042"]["machine_id"] == "machine-issue"


def test_racing_issue_claim_cannot_overwrite_a_winner(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _phases, issues, rival = board
    original = _git
    intercepted = False

    def race(repo: str | Path, *args: str, **kwargs: Any) -> tuple[int, str, str]:
        nonlocal intercepted
        if args[0] == "push" and not intercepted:
            intercepted = True
            rival.reserve(1234567, "machine-winner")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", race)
    with pytest.raises(ReservationConflict, match="issue 1234567"):
        issues.reserve(1234567, "machine-loser")
    assert issues.read()[0]["phases"]["1234567"]["machine_id"] == "machine-winner"


def test_pending_writeback_keeps_ownership_until_posted(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    _phases, issues, rival = board
    token = issues.reserve(42, "machine-1")
    receipt = {"state_reason": "writeback_pending", "body": "Review is ready."}
    issues.set_issue_receipt(token, receipt)
    assert issues.release_group(token) == []
    assert issues.expire_stale(dt.datetime.now(dt.UTC) + dt.timedelta(days=7)) == []
    with pytest.raises(ReservationConflict):
        rival.reserve(42, "machine-2")
    recovered = issues.takeover(
        42,
        "machine-1",
        confirm=True,
        expected_reservation_id=token["reservation_id"],
        expected_generation=token["generation"],
    )
    assert recovered["completion_receipt"] == receipt
    with pytest.raises(ReservationFenced):
        issues.set_issue_receipt(token, None)
    issues.set_issue_receipt(recovered, None)
    assert issues.release_group(recovered) == ["000042"]
    assert rival.reserve(42, "machine-2")["machine_id"] == "machine-2"


def test_released_escalation_blocks_claims_until_cleared(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    _phases, issues, rival = board
    token = issues.reserve(42, "machine-1")
    receipt = {"state_reason": "remedy_escalated", "cleared": False}
    issues.set_issue_receipt(token, receipt)
    assert issues.release_group(token) == ["000042"]
    record = rival.read()[0]["phases"]["000042"]
    assert record["completion_receipt"] == receipt
    assert record["status"] == "expired"
    assert "cooldown_until" not in record
    with pytest.raises(ReservationConflict, match="clearance"):
        rival.reserve(42, "machine-2")
    with pytest.raises(ReservationFenced, match="expired"):
        issues.assert_owner(token)
    issues.set_issue_receipt(token, {**receipt, "cleared": True})
    assert rival.reserve(42, "machine-2")["machine_id"] == "machine-2"


def test_issue_tokens_use_issue_run_directory(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    _phases, issues, _rival = board
    token = issues.reserve(1234567, "machine-1")
    path = reservation_file(issues.root, 1234567, kind="issue")
    assert path == issues.root / "runs" / "issue-1234567" / "reservation.json"
    save_reservation_file(path, token)
    saved = load_reservation_file(path)
    assert saved is not None and saved["issue"] == 1234567
    assert "phase" not in saved


def test_deleted_issue_state_is_not_read_as_empty(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    _phases, issues, rival = board
    issues.reserve(42, "machine-1")
    assert _git(rival.private, "fetch", "origin", issues.branch)[0] == 0
    assert (
        _git(rival.private, "checkout", "--detach", f"origin/{issues.branch}")[0] == 0
    )
    assert _git(rival.private, "rm", state_rel(issues=True).as_posix())[0] == 0
    assert _git(rival.private, "commit", "-qm", "remove issue state")[0] == 0
    assert _git(rival.private, "push", "origin", f"HEAD:{issues.branch}")[0] == 0
    with pytest.raises(CoordinationUnavailable, match="deleted"):
        issues.read()


def test_cli_clears_only_a_confirmed_escalation_with_its_fence(
    board: tuple[ReservationStore, ReservationStore, ReservationStore],
) -> None:
    _phases, issues, _rival = board
    document = copy.deepcopy(BASE)
    document["repo-root"] = str(issues.root)
    document["board"] = {
        "repo": str(issues.private),
        "branch": issues.branch,
        "remote": issues.remote,
        "file": "coordination/phases.json",
        "issues-file": "coordination/issues.json",
    }
    config = write(issues.root.parent, json.dumps(document))

    def invoke(
        token: dict[str, Any], *, confirm: bool = True
    ) -> subprocess.CompletedProcess[str]:
        args = [
            sys.executable,
            "-m",
            "argus.board.reservations",
            "clear-escalation",
            "--config",
            str(config),
            "--issues",
            "--phase",
            str(token["issue"]),
            "--machine-id",
            token["machine_id"],
            "--reservation-id",
            token["reservation_id"],
            "--generation",
            str(token["generation"]),
        ]
        if confirm:
            args.append("--confirm")
        return subprocess.run(
            args,
            cwd=issues.root,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    token = issues.reserve(42, "machine-1")
    issues.set_issue_receipt(
        token, {"state_reason": "remedy_escalated", "cleared": False}
    )
    issues.release_group(token)
    assert invoke(token, confirm=False).returncode == 2
    assert invoke({**token, "generation": token["generation"] + 1}).returncode == 4
    cleared = invoke(token)
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert (
        json.loads(cleared.stdout)["reservation"]["completion_receipt"]["cleared"]
        is True
    )
    pending = issues.reserve(43, "machine-1")
    issues.set_issue_receipt(
        pending, {"state_reason": "writeback_pending", "body": "Review is ready."}
    )
    refused = invoke(pending)
    assert refused.returncode == 2
    assert (
        issues.read()[0]["phases"]["000043"]["completion_receipt"]["state_reason"]
        == "writeback_pending"
    )
