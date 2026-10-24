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
