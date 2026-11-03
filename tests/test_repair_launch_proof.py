# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A repair launch counts as refused only with proof that no stage started."""

from __future__ import annotations

import json
from pathlib import Path

from argus.gitsync.repair_refusal import proven_refusal, snapshot

FAILED = json.dumps(
    {
        "state": "failed",
        "reason": "run already finished as completed; --fresh to start over",
    }
)


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "repair-7"
    folder.mkdir()
    (folder / "run.json").write_text('{"state": "completed"}', encoding="utf-8")
    return folder


def test_a_startup_refusal_with_its_failed_record_is_proven(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "result.json").write_text(FAILED, encoding="utf-8")
    assert proven_refusal(folder, before) is True


def test_a_stage_that_wrote_a_file_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "task.md").write_text("step\n", encoding="utf-8")
    (folder / "result.json").write_text(FAILED, encoding="utf-8")
    assert proven_refusal(folder, before) is False


def test_a_rewritten_run_json_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "run.json").write_text('{"state": "running"}', encoding="utf-8")
    (folder / "result.json").write_text(FAILED, encoding="utf-8")
    assert proven_refusal(folder, before) is False


def test_a_crash_with_no_failed_record_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "task.md").write_text("step\n", encoding="utf-8")
    assert proven_refusal(folder, before) is False


def test_an_unknown_state_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "result.json").write_text('{"state": "running"}', encoding="utf-8")
    assert proven_refusal(folder, before) is False


def test_an_unreadable_record_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    before = snapshot(folder)
    (folder / "result.json").write_text("{not json", encoding="utf-8")
    assert proven_refusal(folder, before) is False


def test_a_missing_run_folder_spends_a_round(tmp_path: Path) -> None:
    assert proven_refusal(tmp_path / "repair-9", {}) is False


def test_no_result_file_spends_a_round(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    assert proven_refusal(folder, snapshot(folder)) is False
