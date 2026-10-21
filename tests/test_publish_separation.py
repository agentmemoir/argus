# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Publication checks the Git destinations before it pushes anything."""

import json
from pathlib import Path

import pytest

import argus.gitsync.group_review as group_review
from argus import conventions
from argus.gitsync.git import git
from argus.gitsync.modes import mode_published
from argus.gitsync.publish_phase import publish_phase
from argus.gitsync.selftest_publish import _publish_fixture


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


def test_separate_destinations_publish(tmp_path: Path) -> None:
    pub, _nested, run_dir = _publish_fixture(tmp_path, "separate", 100451)
    result = publish_phase(pub, 100451, pull_request="https://example.test/pr/1")
    assert result["ok"], result
    assert result["state"] == "AWAITING_REVIEW", result
    assert (run_dir / "publication.json").is_file()


def test_no_pull_request_returns_retry_without_a_receipt(tmp_path: Path) -> None:
    pub, _nested, run_dir = _publish_fixture(tmp_path, "no-pull-request", 100453)
    result = publish_phase(pub, 100453)
    assert result["ok"] is False, result
    assert result["state"] == "RETRY", result
    assert not (run_dir / "publication.json").exists()


def test_a_shared_destination_returns_retry_without_a_push(tmp_path: Path) -> None:
    pub, nested, run_dir = _publish_fixture(tmp_path, "shared", 100452)
    public_remote = tmp_path / "shared" / "pub-remote.git"
    assert (
        git(nested, "remote", "set-url", "--push", "origin", str(public_remote))[0] == 0
    )
    before = git(public_remote, "for-each-ref", "--format=%(refname) %(objectname)")[1]

    result = publish_phase(pub, 100452)

    assert result["ok"] is False, result
    assert result["state"] == "RETRY", result
    assert not (run_dir / "publication.json").exists()
    after = git(public_remote, "for-each-ref", "--format=%(refname) %(objectname)")[1]
    assert after == before, "a shared destination received a push"


def test_a_foreign_fence_refuses_the_published_write(tmp_path: Path) -> None:
    pub, nested, run_dir = _publish_fixture(tmp_path, "fenced", 100454)
    before = git(nested, "rev-parse", "HEAD")[1]
    reservation_path = run_dir / "reservation.json"
    reservation = json.loads(reservation_path.read_text())
    reservation["machine_id"] = "server-99"
    reservation_path.write_text(json.dumps(reservation) + "\n")

    result = mode_published(pub, 100454)

    assert result["ok"] is False, result
    assert result["state"] == "FENCED", result
    assert not (run_dir / "publication.json").exists()
    assert git(nested, "rev-parse", "HEAD")[1] == before


def test_an_unreadable_reservation_fails_closed(tmp_path: Path) -> None:
    pub, nested, run_dir = _publish_fixture(tmp_path, "unreadable", 100455)
    before = git(nested, "rev-parse", "HEAD")[1]
    (run_dir / "reservation.json").write_text("not json\n")

    result = mode_published(pub, 100455)

    assert result["ok"] is False, result
    assert result["state"] == "COORDINATION_UNAVAILABLE", result
    assert not (run_dir / "publication.json").exists()
    assert git(nested, "rev-parse", "HEAD")[1] == before


def test_group_ready_record_covers_every_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_calls: list[tuple[object, ...]] = []
    check_calls: list[tuple[object, ...]] = []
    persist_calls: list[bool] = []

    def marker(root: str | Path, phase: int) -> Path:
        return Path(root) / "runs" / f"phase-{phase:06d}" / "group-pr-ready.json"

    def checks(*args: object) -> tuple[list[dict[str, str]], int, str]:
        check_calls.append(args)
        return [{"bucket": "pass"}], 0, ""

    def ready(*args: object) -> tuple[bool, str]:
        attempt = marker(tmp_path, 100455)
        assert json.loads(attempt.read_text())["version"] == 2
        assert persist_calls == [True]
        ready_calls.append(args)
        return True, "ready"

    def persist_attempt() -> dict[str, object]:
        persist_calls.append(marker(tmp_path, 100455).is_file())
        return {"ok": True}

    def history(*_args: object) -> tuple[dict[str, object], str]:
        if ready_calls:
            return {
                "state": "OPEN",
                "is_draft": False,
                "events": [{"type": "ReadyForReviewEvent", "created_at": "now"}],
            }, ""
        return {
            "state": "OPEN",
            "is_draft": True,
            "events": [],
        }, ""

    monkeypatch.setattr(group_review, "_group_ready_marker", marker)
    monkeypatch.setattr(group_review, "gh_pull_request_checks", checks)
    monkeypatch.setattr(group_review, "gh_mark_pull_request_ready", ready)
    monkeypatch.setattr(group_review, "gh_pull_request_stage_history", history)
    members = ["100454", "100455"]
    pull_request = "https://github.com/example-org/example-repo/pull/7"

    first = group_review.mark_group_pull_request_ready(
        tmp_path, 100455, members, [], pull_request, persist_attempt=persist_attempt
    )
    sibling = group_review.mark_group_pull_request_ready(
        tmp_path, 100454, members, [], pull_request, persist_attempt=persist_attempt
    )

    assert first is not None and first["ok"] is True
    assert sibling is not None and sibling["skipped"] is True
    assert len(ready_calls) == 1
    assert persist_calls == [True]
    assert len(check_calls) == 1
