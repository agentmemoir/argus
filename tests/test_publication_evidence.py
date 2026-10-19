# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A receipt needs every pushed head and a pull request before it is published."""

import json
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync.publish import mark_phase_published


def _receipt(tmp_path: Path, **fields: object) -> Path:
    runs = tmp_path / "runs"
    run = runs / "phase-000001"
    run.mkdir(parents=True)
    receipt = {
        "version": 1,
        "phase": "000001",
        "state": "awaiting_review",
        "branch": "feature/x",
        "private_branch": "master",
        "public_head": "a" * 40,
        "private_head": "b" * 40,
        "pull_request": "https://github.com/example/repo/pull/1",
    }
    receipt.update(fields)
    path = run / "publication.json"
    path.write_text(json.dumps(receipt) + "\n")
    return path


@pytest.fixture(autouse=True)
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conventions,
        "_settings",
        {"repo-root": tmp_path, "driver.runs": tmp_path / "runs", "pipeline": None},
    )


def test_a_missing_pushed_head_is_not_published(tmp_path: Path) -> None:
    path = _receipt(tmp_path, public_head=None, missing_heads=["public"])
    result = mark_phase_published(tmp_path, 1)
    assert result["ok"]
    assert result["action"] == "no-publish-evidence", result
    assert json.loads(path.read_text())["state"] == "awaiting_review"


def test_a_receipt_without_a_pull_request_is_not_published(tmp_path: Path) -> None:
    path = _receipt(tmp_path, pull_request=None)
    result = mark_phase_published(tmp_path, 1)
    assert result["ok"]
    assert result["action"] == "no-publish-evidence", result
    assert json.loads(path.read_text())["state"] == "awaiting_review"
