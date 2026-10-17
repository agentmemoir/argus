# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A round that breaks the scope it changed keeps its budget, and the next task names it."""

import json
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import gh_gate, repair, repair_hold
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
    state: dict[str, Any] = {"head": "head-0", "limit": 3, "wait": 0}

    def fake_setting(key: str, default: Any = None) -> Any:
        return {
            "repair.wait": state["wait"],
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
    # The hold tally lives in its own module, so it reads the same two.
    monkeypatch.setattr(repair_hold, "setting", fake_setting)
    monkeypatch.setattr(repair_hold, "runs_dir", fake_runs_dir)
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


def record_file(root: Path) -> Path:
    return root / "runs" / ".driver" / "repair" / "pr-201.json"


def record(root: Path) -> dict[str, Any]:
    return json.loads(record_file(root).read_text(encoding="utf-8"))


def write_check(
    root: Path, number: int, rc: int | None, nonce: str | None = None
) -> None:
    """What the driver leaves behind after it runs the scope check of one round.

    ``rc`` is the check's exit code, or ``None`` for a check still running.
    ``nonce`` is the round the answer belongs to, wrong on purpose to stand for
    the answer of another round.
    """
    folder = round_folder(root, number)
    folder.mkdir(parents=True, exist_ok=True)
    round_nonce = nonce if nonce is not None else str(record(root)["nonce"])
    (folder / "verify.id").write_text(f"{round_nonce}\n", encoding="utf-8")
    (folder / "verify.paths").write_text("crates/x/src/lib.rs\n", encoding="utf-8")
    (folder / "verify.command").write_text("make scope\n", encoding="utf-8")
    (folder / "verify.log").write_text("test result: FAILED\n", encoding="utf-8")
    if rc is not None:
        (folder / "verify.rc").write_text(f"{rc}\n", encoding="utf-8")


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


def test_a_check_still_running_holds_the_next_round(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, None)
    env["wait"] = 1800
    found = next_round(tmp_path, env)
    assert found["state"] == "HOLD"
    assert "has not finished" in str(found["summary"])
    assert record(tmp_path)["attempts"] == 0
    assert record(tmp_path)["rounds"] == 1


def test_a_check_that_never_answers_is_counted_after_the_wait(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, None)
    found = next_round(tmp_path, env)
    assert found["state"] == "REPAIR" and found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
    assert "regression" not in str(found["summary"])


def test_the_answer_belongs_to_the_round_that_started_it(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    """A started check holds; the same files under another round's id read as none."""
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, None)
    env["wait"] = 1800
    assert next_round(tmp_path, env)["state"] == "HOLD"
    (round_folder(tmp_path, 1) / "verify.id").write_text("1-1\n", encoding="utf-8")
    env["wait"] = 0
    found = next_round(tmp_path, env)
    assert found["state"] == "REPAIR" and found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
    assert "regression" not in str(found["summary"])


def test_a_later_cycle_does_not_read_the_previous_cycles_answer(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, 101, nonce="1-1")
    # The checks went green, so the record that carried the round was deleted.
    record_file(tmp_path).unlink()
    assert next_round(tmp_path, env)["id"] == "201-1"
    # The later red result starts again at round 1, and its own check has not
    # answered: the earlier cycle's answer must not be read for it.
    assert record(tmp_path)["nonce"] != "1-1"
    found = next_round(tmp_path, env)
    assert found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
    assert "regression" not in str(found["summary"])


def test_the_task_header_names_the_attempt_and_the_round(
    tmp_path: Path, env: dict[str, Any]
) -> None:
    assert mode_repair(tmp_path)["id"] == "201-1"
    write_check(tmp_path, 1, 101)
    assert next_round(tmp_path, env)["id"] == "201-2"
    write_check(tmp_path, 2, 101)
    found = next_round(tmp_path, env)
    assert found["id"] == "201-3"
    task = Path(str(found["task"])).read_text(encoding="utf-8")
    assert "Attempt: 1 of 3 (round 3)" in task


@pytest.mark.usefixtures("env")
def test_a_record_written_before_rounds_existed_still_counts(tmp_path: Path) -> None:
    path = tmp_path / "runs" / ".driver" / "repair" / "pr-201.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {"attempts": 1, "notified": False, "head": "", "waiting_since": None}
    path.write_text(json.dumps(old), encoding="utf-8")
    found = mode_repair(tmp_path)
    assert found["id"] == "201-2"
    assert record(tmp_path)["attempts"] == 1
