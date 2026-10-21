# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Publication checks the Git destinations before it pushes anything."""

import json
from pathlib import Path

import pytest

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
