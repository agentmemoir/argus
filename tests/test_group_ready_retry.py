"""Retry rules for the shared group pull-request ready transition."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import argus.gitsync.group_review as group_review


def _history_response(
    stage: dict[str, object] | None, detail: str = ""
) -> Callable[..., tuple[dict[str, object] | None, str]]:
    def read(*_args: object) -> tuple[dict[str, object] | None, str]:
        return stage, detail

    return read


def _group_ready_retry_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[tuple[object, ...]]]:
    def marker(_root: str | Path, phase: int) -> Path:
        return tmp_path / "runs" / f"phase-{phase:06d}" / "group-pr-ready.json"

    def checks(*_args: object) -> tuple[list[dict[str, str]], int, str]:
        return [{"bucket": "pass"}], 0, ""

    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response({"state": "OPEN", "is_draft": True, "events": []}),
    )

    ready_calls: list[tuple[object, ...]] = []

    def ready(*args: object) -> tuple[bool, str]:
        ready_calls.append(args)
        return True, "ready"

    monkeypatch.setattr(group_review, "_group_ready_marker", marker)
    monkeypatch.setattr(group_review, "gh_pull_request_checks", checks)
    monkeypatch.setattr(group_review, "gh_mark_pull_request_ready", ready)
    return marker(tmp_path, 100501), ready_calls


def test_group_ready_marker_failure_prevents_remote_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    persist_calls: list[bool] = []

    def fail_write(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(group_review, "_write_group_ready_attempt", fail_write)
    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: persist_calls.append(True) or {"ok": True},
    )

    assert result is not None and result["ok"] is False
    assert "could not be written" in result["detail"]
    assert not marker.exists()
    assert persist_calls == []
    assert ready_calls == []


def test_group_ready_persistence_failure_prevents_remote_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": False, "summary": "push failed"},
    )

    assert result is not None and result["ok"] is False
    assert "push failed" in result["detail"]
    assert marker.is_file()
    assert ready_calls == []


def test_group_ready_persistence_exception_prevents_remote_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)

    def fail_persist() -> dict[str, object]:
        raise OSError("remote state unavailable")

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=fail_persist,
    )

    assert result is not None and result["ok"] is False
    assert "remote state unavailable" in result["detail"]
    assert marker.is_file()
    assert ready_calls == []


def test_group_ready_retry_stops_after_human_drafts_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "version": 2,
                "pull_request": "https://github.com/example-org/example-repo/pull/7",
                "attempted_at": "now",
            }
        )
    )
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {
                "state": "OPEN",
                "is_draft": True,
                "events": [
                    {"type": "ReadyForReviewEvent", "created_at": "then"},
                    {"type": "ConvertToDraftEvent", "created_at": "now"},
                ],
            }
        ),
    )

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": True},
    )

    assert result is not None and result["skipped"] is True
    assert ready_calls == []


def test_group_ready_markerless_retry_stops_after_human_drafts_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {
                "state": "OPEN",
                "is_draft": True,
                "events": [
                    {"type": "ReadyForReviewEvent", "created_at": "then"},
                    {"type": "ConvertToDraftEvent", "created_at": "now"},
                ],
            }
        ),
    )

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": True},
    )

    assert not marker.exists()
    assert result is not None and result["skipped"] is True
    assert ready_calls == []


def test_group_ready_retry_stops_after_successful_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "version": 2,
                "pull_request": "https://github.com/example-org/example-repo/pull/7",
                "attempted_at": "now",
            }
        )
    )
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {
                "state": "OPEN",
                "is_draft": False,
                "events": [{"type": "ReadyForReviewEvent", "created_at": "then"}],
            }
        ),
    )

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": True},
    )

    assert result is not None and result["skipped"] is True
    assert ready_calls == []


def test_group_ready_retry_uses_clear_draft_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "version": 2,
                "pull_request": "https://github.com/example-org/example-repo/pull/7",
                "attempted_at": "now",
            }
        )
    )
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response({"state": "OPEN", "is_draft": True, "events": []}),
    )

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": True},
    )

    assert result is not None and result["ok"] is True
    assert len(ready_calls) == 1


def test_group_ready_retry_stops_when_history_is_unclear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, ready_calls = _group_ready_retry_setup(tmp_path, monkeypatch)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "version": 2,
                "pull_request": "https://github.com/example-org/example-repo/pull/7",
                "attempted_at": "now",
            }
        )
    )
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(None, "GitHub did not answer"),
    )

    result = group_review.mark_group_pull_request_ready(
        tmp_path,
        100501,
        ["100500", "100501"],
        [],
        "https://github.com/example-org/example-repo/pull/7",
        persist_attempt=lambda: {"ok": True},
    )

    assert result is not None and result["ok"] is False
    assert result["detail"] == "GitHub did not answer"
    assert ready_calls == []
