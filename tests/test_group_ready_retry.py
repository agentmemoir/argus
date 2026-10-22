# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Retry rules for the shared group pull-request ready transition.

The once-guard is GitHub's own ready/draft timeline. There is no local ready
record, so every case here drives the answer from the history stubbed at the
``gh`` boundary and asserts that nothing is written to the run directory.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import argus.gitsync.group_review as group_review

URL = "https://github.com/example-org/example-repo/pull/7"
MEMBERS = ["100500", "100501"]


def _history_response(
    stage: dict[str, object] | None, detail: str = ""
) -> Callable[..., tuple[dict[str, object] | None, str]]:
    def read(*_args: object) -> tuple[dict[str, object] | None, str]:
        return stage, detail

    return read


def _checks_returning(
    rows: list[dict[str, str]], code: int, detail: str
) -> Callable[..., tuple[list[dict[str, str]], int, str]]:
    def read(*_args: object) -> tuple[list[dict[str, str]], int, str]:
        return rows, code, detail

    return read


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Callable[..., dict[str, object] | None], list[tuple[object, ...]]]:
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

    monkeypatch.setattr(group_review, "gh_pull_request_checks", checks)
    monkeypatch.setattr(group_review, "gh_mark_pull_request_ready", ready)

    def call() -> dict[str, object] | None:
        return group_review.mark_group_pull_request_ready(
            tmp_path, 100501, MEMBERS, [], URL
        )

    return call, ready_calls


def _ready_events() -> list[dict[str, str]]:
    return [
        {"type": "ReadyForReviewEvent", "created_at": "then"},
        {"type": "ConvertToDraftEvent", "created_at": "now"},
    ]


def test_group_ready_retry_stops_after_human_drafts_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {"state": "OPEN", "is_draft": True, "events": _ready_events()}
        ),
    )

    result = call()

    assert result is not None and result["skipped"] is True
    assert "prior ready" in str(result["detail"])
    assert ready_calls == []


def test_group_ready_retry_stops_after_successful_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
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

    result = call()

    assert result is not None and result["skipped"] is True
    assert ready_calls == []


def test_group_ready_retry_stops_after_a_merged_prior_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A merged pull request with a ready event is still a ready that happened;
    # the retry must not re-open the transition on it.
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {
                "state": "MERGED",
                "is_draft": False,
                "events": [{"type": "ReadyForReviewEvent", "created_at": "then"}],
            }
        ),
    )

    result = call()

    assert result is not None and result["skipped"] is True
    assert ready_calls == []


def test_group_ready_retry_uses_clear_draft_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)

    result = call()

    assert result is not None and result["ok"] is True
    assert len(ready_calls) == 1
    assert not (tmp_path / "runs").exists(), "the ready wrote a local record"


def test_group_ready_retry_stops_when_history_is_unclear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(None, "GitHub did not answer"),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert result["detail"] == "GitHub did not answer"
    assert ready_calls == []


def test_group_ready_retry_stops_when_a_stage_transition_is_unclear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Some other timeline event: not a ready, and not provably a draft nobody
    # touched. Fail closed rather than guess.
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response(
            {
                "state": "OPEN",
                "is_draft": True,
                "events": [{"type": "ConvertToDraftEvent", "created_at": "then"}],
            }
        ),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert "unclear stage transition" in str(result["detail"])
    assert ready_calls == []


def test_group_ready_retry_stops_when_the_stage_does_not_prove_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An open pull request that is not a draft and has no ready event: the
    # ready already happened, or a person changed it; either way, no write.
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response({"state": "OPEN", "is_draft": False, "events": []}),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert "does not prove a safe ready retry" in str(result["detail"])
    assert ready_calls == []


def test_group_ready_retry_stops_when_the_history_is_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_stage_history",
        _history_response({"state": "OPEN", "is_draft": "yes", "events": []}),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert "unclear" in str(result["detail"])
    assert ready_calls == []


def test_group_ready_retry_refuses_an_empty_check_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_checks",
        _checks_returning([], 0, ""),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert "not all green" in str(result["detail"])
    assert ready_calls == []


def test_group_ready_retry_refuses_a_pending_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, ready_calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        group_review,
        "gh_pull_request_checks",
        _checks_returning([{"bucket": "pending"}], 8, "one check is still running"),
    )

    result = call()

    assert result is not None and result["ok"] is False
    assert result["detail"] == "one check is still running"
    assert ready_calls == []


def test_group_ready_does_nothing_while_a_member_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call, ready_calls = _setup(tmp_path, monkeypatch)

    assert (
        group_review.mark_group_pull_request_ready(
            tmp_path, 100501, MEMBERS, ["100500"], URL
        )
        is None
    )
    assert ready_calls == []


def test_group_ready_report_is_json_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call, _ready_calls = _setup(tmp_path, monkeypatch)

    result = call()

    assert result is not None
    json.dumps(result)
