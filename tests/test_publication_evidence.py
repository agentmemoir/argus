# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A receipt needs every pushed head and a pull request before it is published."""

import json
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync.git import ConfigError
from argus.gitsync.group_publication import _write_publication_receipt
from argus.gitsync.publish import mark_phase_published


def _receipt(tmp_path: Path, **fields: object) -> Path:
    runs = tmp_path / "runs"
    run = runs / "phase-000001"
    run.mkdir(parents=True)
    receipt: dict[str, object] = {
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


def _reservation() -> dict[str, object]:
    return {"machine_id": "m", "reservation_id": "r", "generation": 1}


def _stub_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, head: str | None
) -> None:
    module = "argus.gitsync.group_publication"

    def runstate(*_args: object, **_kwargs: object) -> Path:
        return tmp_path

    def run_dir(*_args: object, **_kwargs: object) -> Path:
        return tmp_path

    def no_group(*_args: object, **_kwargs: object) -> None:
        return None

    def published(*_args: object, **_kwargs: object) -> str | None:
        return head

    def branch(*_args: object, **_kwargs: object) -> str:
        return "main"

    monkeypatch.setattr(f"{module}.runstate_repo", runstate)
    monkeypatch.setattr(f"{module}.phase_run_dir", run_dir)
    monkeypatch.setattr(f"{module}._declared_group", no_group)
    monkeypatch.setattr(f"{module}._published_head", published)
    monkeypatch.setattr(f"{module}.current_branch", branch)
    monkeypatch.setattr(f"{module}.integration_branch", branch)


def test_the_receipt_writer_refuses_a_missing_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_writer(monkeypatch, tmp_path, None)
    with pytest.raises(ConfigError):
        _write_publication_receipt(
            tmp_path,
            1,
            _reservation(),
            [],
            pull_request="https://example.test/pr/1",
        )


def test_the_receipt_writer_refuses_no_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_writer(monkeypatch, tmp_path, "a" * 40)
    results = [
        {"repo": str(tmp_path), "ok": True, "action": "pushed", "after": "a" * 40},
    ]
    with pytest.raises(ConfigError):
        _write_publication_receipt(tmp_path, 1, _reservation(), results)
