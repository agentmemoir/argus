# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A round that breaks the scope it changed keeps its budget, and the next task names it."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import gh_gate, repair
from argus.gitsync.repair import mode_repair

PR_URL = "https://github.com/agentmemoir/clio/pull/201"
FAILING = [{"bucket": "fail", "name": "checks", "link": "https://example.test/1"}]
RECEIPT: dict[str, Any] = {
    "state": "awaiting_review",
    "pull_request": PR_URL,
    "branch": "feature/x",
    "reservation": {"machine_id": "m1"},
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """One published pull request whose head moves on each push, and a repair limit."""
    state: dict[str, Any] = {"head": "head-0", "limit": 3}

    def fake_setting(key: str, default: Any = None) -> Any:
        return {
            "repair.wait": 0,
            "repair.attempts": state["limit"],
            "driver.state": ".driver",
            "driver.merge-check-max": 5,
            "machine.id": "m1",
        }.get(key, default)

    def fake_head(*_args: object, **_kwargs: object) -> gh_gate.GhRead[tuple[str, str]]:
        return gh_gate.GhRead(("OPEN", state["head"]), "")

    def fake_checks(
        *_args: object, **_kwargs: object
    ) -> gh_gate.GhRead[list[dict[str, Any]]]:
        return gh_gate.GhRead(FAILING, "")

    def fake_runs_dir(_root: object) -> Path:
        return tmp_path / "runs"

    def fake_entries(_root: object) -> list[tuple[int, dict[str, Any]]]:
        return [(201, {})]

    def fake_receipt(_root: object, _number: object) -> dict[str, Any]:
        return RECEIPT

    def fake_checkout(_root: object, _branch: object) -> None:
        return None

    def fake_logs(_ref: object, _rows: object) -> str:
        return "##[error] cause"

    def fake_without_run(_rows: object) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(repair, "setting", fake_setting)
    monkeypatch.setattr(repair, "runs_dir", fake_runs_dir)
    monkeypatch.setattr(repair, "gh_pull_request_head", fake_head)
    monkeypatch.setattr(repair, "gh_pull_request_checks", fake_checks)
    monkeypatch.setattr(repair, "_phase_run_entries", fake_entries)
    monkeypatch.setattr(repair, "phase_receipt", fake_receipt)
    monkeypatch.setattr(repair, "_checkout", fake_checkout)
    monkeypatch.setattr(repair, "_failure_logs", fake_logs)
    monkeypatch.setattr(repair, "checks_without_run", fake_without_run)
    return state


def round_folder(root: Path, number: int) -> Path:
    return root / "runs" / ".driver" / "repair" / f"201-{number}"


def record(root: Path) -> dict[str, Any]:
    path = root / "runs" / ".driver" / "repair" / "pr-201.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_check(root: Path, number: int, rc: int) -> None:
    """What the driver leaves behind after it runs the scope check of one round."""
    folder = round_folder(root, number)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "verify.rc").write_text(f"{rc}\n", encoding="utf-8")
    (folder / "verify.paths").write_text("crates/x/src/lib.rs\n", encoding="utf-8")
    (folder / "verify.command").write_text("make scope\n", encoding="utf-8")
    (folder / "verify.log").write_text("test result: FAILED\n", encoding="utf-8")


def next_round(root: Path, env: dict[str, Any]) -> dict[str, Any]:
    """Move the pull request's head as a push would, then ask for the next turn."""
    head = int(str(env["head"]).split("-")[1]) + 1
    env["head"] = f"head-{head}"
    found: dict[str, Any] = mode_repair(root)
    return found


def test_a_round_with_no_check_behind_it_is_counted(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    first = mode_repair(tmp_path)
    assert first["state"] == "REPAIR" and first["id"] == "201-1"
    assert first["base"] == "head-0"
    assert record(tmp_path)["attempts"] == 0
    second = next_round(tmp_path, env)
    assert second["state"] == "REPAIR" and second["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
    assert "regression" not in str(second["summary"])


def test_a_round_that_passes_its_own_scope_is_counted(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, 0)
    found = next_round(tmp_path, env)
    assert found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
    assert "regression" not in str(found["summary"])


def test_a_round_that_fails_its_own_scope_keeps_its_budget(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, 101)
    found = next_round(tmp_path, env)
    assert found["state"] == "REPAIR" and found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 0
    assert "self-introduced regression" in str(found["summary"])
    task = Path(str(found["task"])).read_text(encoding="utf-8")
    assert "### The last round broke the scope it changed" in task
    assert "exited 101" in task and "crates/x/src/lib.rs" in task
    assert "test result: FAILED" in task


def test_the_budget_still_ends_a_repair_that_passes_its_scope(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    for number in (1, 2, 3):
        assert mode_repair(tmp_path)["id"] == f"201-{number}"
        write_check(tmp_path, number, 0)
    found = next_round(tmp_path, env)
    assert found["state"] == "EXHAUSTED"
    assert "3 of 3 attempt(s)" in str(found["summary"])


def test_repeated_regressions_end_at_the_round_cap(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    for number in (1, 2, 3, 4, 5, 6):
        assert mode_repair(tmp_path)["id"] == f"201-{number}"
        write_check(tmp_path, number, 1)
    found = next_round(tmp_path, env)
    assert found["state"] == "EXHAUSTED" and found["notify"] is True
    assert "0 of 3 attempt(s) and 6 round(s)" in str(found["summary"])
    assert "self-introduced regression" in str(found["summary"])


@pytest.mark.usefixtures("env")
def test_a_record_written_before_rounds_existed_still_counts(tmp_path: Path) -> None:
    path = tmp_path / "runs" / ".driver" / "repair" / "pr-201.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {"attempts": 1, "notified": False, "head": "", "waiting_since": None}
    path.write_text(json.dumps(old), encoding="utf-8")
    found = mode_repair(tmp_path)
    assert found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
