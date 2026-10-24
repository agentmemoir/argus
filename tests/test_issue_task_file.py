# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""An issue tick's stall check finds the frozen task file in the issue task folder."""

import json
from pathlib import Path

import pytest

from argus import conventions
from config_issue_fixture import issue_document
from driver_maintenance_support import shell

PIPELINE = """run_dir: runs/issue-{issue_number:06d}
inputs:
  issue_number: {type: int, required: true}
  issue_file: {type: str, required: true}
"""
TASK = "issue-000255-fix-api.md"


@pytest.fixture
def issue_driver(tmp_path: Path) -> Path:
    document = issue_document()
    document["repo-root"] = "."
    document["pipeline"] = "pipeline.yaml"
    document["number-input"] = "issue_number"
    document["file-input"] = "issue_file"
    document["issues"]["task-dir"] = "runs/issue-tasks"
    (tmp_path / "pipeline.yaml").write_text(PIPELINE, encoding="utf-8")
    (tmp_path / "machine.id").write_text("machine-1\n", encoding="utf-8")
    (tmp_path / "fixture.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def stall_check(root: Path) -> str:
    body = (
        "work_kind=issue; PHASE_FILE_INPUT=issue_file\n"
        "printf '255\\n' >\"$CURRENT\"\n"
        "check_stall\n"
        "printf 'checked\\n'\n"
    )
    return shell(root, body)


def test_issue_tick_without_task_file_refuses_in_one_line(issue_driver: Path) -> None:
    assert "checked" in stall_check(issue_driver)
    events = (issue_driver / "events").read_text(encoding="utf-8").splitlines()
    assert any("issue 255" in line and "issue_file" in line for line in events)


def test_issue_tick_finds_task_file_in_issue_folder(issue_driver: Path) -> None:
    folder = issue_driver / "runs" / "issue-tasks"
    folder.mkdir(parents=True)
    (folder / TASK).write_text("frozen task\n", encoding="utf-8")
    assert "checked" in stall_check(issue_driver)
    assert not (issue_driver / "events").exists()


def abandon_issue(root: Path) -> str:
    body = (
        '. "$TEST_DRIVER/lib-driver-abandon.sh"\n'
        "work_kind=issue; PHASE_FILE_INPUT=issue_file\n"
        '(abandon_cmd 255) 2>"$RUN_DIR/abandon.err" || true\n'
        "printf 'done\\n'\n"
    )
    return shell(root, body)


def test_issue_abandon_without_task_file_refuses_in_one_line(
    issue_driver: Path,
) -> None:
    assert "done" in abandon_issue(issue_driver)
    err = (issue_driver / "abandon.err").read_text(encoding="utf-8")
    assert "Traceback" not in err
    assert "issue 255 has no task file" in err and "issue_file" in err


def test_issue_abandon_finds_task_file_in_issue_folder(issue_driver: Path) -> None:
    folder = issue_driver / "runs" / "issue-tasks"
    folder.mkdir(parents=True)
    (folder / TASK).write_text("frozen task\n", encoding="utf-8")
    assert "done" in abandon_issue(issue_driver)
    err = (issue_driver / "abandon.err").read_text(encoding="utf-8")
    assert "Traceback" not in err
    assert "no run directory at" in err and "issue-000255" in err


def resume_issue(root: Path, call: str) -> str:
    body = (
        '. "$TEST_DRIVER/lib-driver-halt-record.sh"\n'
        '. "$TEST_DRIVER/lib-driver-resume.sh"\n'
        "work_kind=issue; PHASE_FILE_INPUT=issue_file; RESUME_FROM_TICK=0\n"
        f"{call}\n"
        "printf 'done\\n'\n"
    )
    return shell(root, body)


def test_issue_resume_without_task_file_refuses_before_any_state(
    issue_driver: Path,
) -> None:
    call = '(_drive_resume 255) 2>"$RUN_DIR/resume.err" || true'
    assert "done" in resume_issue(issue_driver, call)
    err = (issue_driver / "resume.err").read_text(encoding="utf-8")
    assert "Traceback" not in err
    assert "issue 255 has no task file" in err and "issue_file" in err
    assert not (issue_driver / "halted").exists()


def test_issue_resume_lookup_finds_task_file_in_issue_folder(
    issue_driver: Path,
) -> None:
    folder = issue_driver / "runs" / "issue-tasks"
    folder.mkdir(parents=True)
    (folder / TASK).write_text("frozen task\n", encoding="utf-8")
    call = 'resume_phase_file_of 255 > "$RUN_DIR/rel.txt"'
    assert "done" in resume_issue(issue_driver, call)
    rel = (issue_driver / "rel.txt").read_text(encoding="utf-8").strip()
    assert rel == f"runs/issue-tasks/{TASK}"


@pytest.fixture
def configured(tmp_path: Path):
    previous = conventions.configure(
        {"repo-root": tmp_path, "issues.task-dir": Path("runs/issue-tasks")}
    )
    yield tmp_path
    conventions.configure(previous)


def test_issue_lookup_matches_number_and_slug_only(configured: Path) -> None:
    folder = configured / "runs" / "issue-tasks"
    folder.mkdir(parents=True)
    (folder / TASK).write_text("frozen task\n", encoding="utf-8")
    (folder / "issue-002550-other.md").write_text("other\n", encoding="utf-8")
    assert conventions.resolve_issue_file(configured, 255) == folder / TASK
    assert conventions.resolve_issue_file(configured, 25) is None
    assert conventions.resolve_issue_file(configured, 256) is None


def tick_run_dirs(root: Path) -> str:
    body = (
        '. "$TEST_DRIVER/lib-driver-job.sh"\n'
        "work_kind=issue; PHASE_FILE_INPUT=issue_file\n"
        'failure_residue_write 255 "" failed 1 0 2>"$RUN_DIR/residue.err"\n'
        "printf 'done\\n'\n"
    )
    return shell(root, body)


def test_issue_run_dir_with_task_file_does_not_raise(issue_driver: Path) -> None:
    folder = issue_driver / "runs" / "issue-tasks"
    folder.mkdir(parents=True)
    (folder / TASK).write_text("frozen task\n", encoding="utf-8")
    assert "done" in tick_run_dirs(issue_driver)
    for name in ("residue.err",):
        assert "Traceback" not in (issue_driver / name).read_text(encoding="utf-8")
    assert (issue_driver / "runs" / "issue-000255" / "failed.json").is_file()


def test_issue_run_dir_without_task_file_logs_one_line(issue_driver: Path) -> None:
    assert "done" in tick_run_dirs(issue_driver)
    for name in ("residue.err",):
        assert "Traceback" not in (issue_driver / name).read_text(encoding="utf-8")
    events = (issue_driver / "events").read_text(encoding="utf-8").splitlines()
    named = [
        line
        for line in events
        if "issue 255 has no task file" in line and "issue_file" in line
    ]
    assert len(named) == 1
    assert not (issue_driver / "runs" / "issue-000255").exists()
