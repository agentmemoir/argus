# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scan`` reports run work that no live claim covers."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.cli import main
from argus.board.git import _git
from argus.board.selftest_fixture import _git_test_repo
from argus.board.store import ReservationStore


def _no_config_file(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture
def scan_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReservationStore:
    """A real board pair and a runs folder the scan can read."""
    root = tmp_path / "project"
    root.mkdir()
    git = shutil.which("git")
    assert git is not None
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": root,
            "driver.runs": root / "runs",
            "board.repo": tmp_path / "scan-client-1",
            "board.branch": "scan-board",
            "board.remote": "origin",
            "board.file": "coordination/phases.json",
            "commands.git": git,
        },
    )
    monkeypatch.setattr(conventions, "configure_from_file", _no_config_file)
    _git_test_repo(tmp_path, "scan")
    store = ReservationStore(root)
    store.migrate_board()
    return store


def _run(scan_store: ReservationStore, phase: str, state: str = "running") -> Path:
    directory = scan_store.root / "runs" / f"phase-{phase}"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"state": state}), encoding="utf-8")
    return directory


def _scan(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert main(["scan", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_scan_does_not_report_a_live_claim(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100001")
    scan_store.reserve(100001, "machine-a")
    scanned = _scan(capsys, "--machine-id", "machine-a")
    assert scanned == {"state": "OK", "phases": []}


def test_scan_reports_no_claim_for_run_work_without_a_record(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100002")
    scanned = _scan(capsys)
    assert scanned["phases"] == [
        {"phase": "100002", "machine_id": None, "status": None, "reason": "no_claim"}
    ]


def test_scan_reports_an_expired_claim(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100003")
    reserved = scan_store.reserve(100003, "machine-a")
    scan_store.set_status(
        {
            "phase": "100003",
            "machine_id": reserved["machine_id"],
            "reservation_id": reserved["reservation_id"],
            "generation": reserved["generation"],
        },
        "expired",
    )
    scanned = _scan(capsys)
    assert [(item["phase"], item["reason"]) for item in scanned["phases"]] == [
        ("100003", "expired")
    ]


def test_scan_reports_another_owners_live_claim(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100004")
    scan_store.reserve(100004, "machine-b")
    scanned = _scan(capsys, "--machine-id", "machine-a")
    assert scanned["phases"] == [
        {
            "phase": "100004",
            "machine_id": "machine-b",
            "status": "claimed",
            "reason": "other_owner",
        }
    ]


def test_scan_does_not_report_a_finished_phase(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100005", state="completed")
    assert _scan(capsys)["phases"] == []


def test_scan_reports_an_unreadable_board_with_exit_two(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(scan_store, "100006")
    _git(scan_store.private, "push", scan_store.remote, "--delete", scan_store.branch)
    assert main(["scan"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "COORDINATION_UNAVAILABLE"
    assert "phases" not in printed


def test_scan_names_a_broken_pipeline_with_exit_two(
    scan_store: ReservationStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pipeline configuration the run folder cannot be resolved under is the
    documented exit 2 with a named error, not a traceback and exit 1."""
    _run(scan_store, "100007")
    pipeline = scan_store.root / "pipeline.yaml"
    pipeline.write_text("run_dir: runs/phase-{phase_number}\n", encoding="utf-8")
    # The fixture's monkeypatch restores the settings attribute at teardown,
    # so the override does not outlive this test.
    conventions.override({"pipeline": str(pipeline)})
    assert main(["scan"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "COORDINATION_UNAVAILABLE"
    assert "pipeline" in printed["error"]


def test_the_documented_command_runs_the_scan() -> None:
    """The `scan` command the CHANGELOG documents is reachable from the module.

    A check that the documented command exists belongs in a test: the
    CHANGELOG once claimed a `scan` verb that no documented command exposed.
    """
    result = subprocess.run(
        [sys.executable, "-m", "argus.board.reservations", "scan", "--help"],
        env=dict(
            os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--machine-id" in result.stdout
    assert "--phase" in result.stdout
