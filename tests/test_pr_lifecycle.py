# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Early drafts, one-shot CI reads, and readiness keep their safety contracts."""

import json
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync.git import ConfigError, git
from argus.gitsync.group_gate import mode_ready
from argus.gitsync.pr_lifecycle import mode_pr
from argus.gitsync.selftest_fixture import BOARD_REL, _configure_self_test, _init_remote
from argus.gitsync.selftest_gate_fixture import URL, _checks, _listing, _RoutedGH
from argus.gitsync.selftest_group_review import _receipt_fixture, _write_receipt


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


@pytest.fixture
def phase_repo(tmp_path: Path) -> Path:
    pub = _init_remote(tmp_path, "draft")[1]
    _configure_self_test(pub, pub / BOARD_REL)
    assert git(pub, "checkout", "-b", "feature/draft")[0] == 0
    return pub


def commit_work(pub: Path) -> None:
    (pub / "work.txt").write_text("work\n")
    assert git(pub, "add", "work.txt")[0] == 0
    assert git(pub, "commit", "-m", "wip: work")[0] == 0


def test_open_existing_pr_ignores_title_and_body(phase_repo: Path) -> None:
    existing = {"number": 7, "url": URL, "title": "edited", "body": "edited"}
    with _RoutedGH({"pr list": _listing(existing)}) as gh:
        out = mode_pr(phase_repo, 10, "open-pr")
    assert out["pull_request"] == existing
    assert not gh.ran("pr create")
    assert (
        json.loads(
            (
                conventions.phase_run_dir(phase_repo, 10) / "pull-request.json"
            ).read_text()
        )["pull_request"]["url"]
        == URL
    )


def test_no_commit_does_not_open_a_pr(phase_repo: Path) -> None:
    with _RoutedGH({"pr list": _listing()}) as gh:
        assert mode_pr(phase_repo, 10, "open-pr")["state"] == "NOT_AHEAD"
    assert not gh.ran("pr create")


def test_first_commit_opens_empty_draft(phase_repo: Path) -> None:
    commit_work(phase_repo)
    with _RoutedGH({"pr list": _listing(), "pr create": {"stdout": URL}}) as gh:
        out = mode_pr(phase_repo, 10, "open-pr")
    assert out["pull_request"]["number"] == 7
    call = gh.ran("pr create")[0]
    assert "--draft" in call
    assert call[call.index("--body") + 1] == ""
    assert call[call.index("--head") + 1] == "feature/draft"


def test_no_pr_peek_records_no_pr_and_never_reads_checks(phase_repo: Path) -> None:
    with _RoutedGH({"pr list": _listing()}) as gh:
        out = mode_pr(phase_repo, 10, "peek-ci")
    assert out["state"] == "NO_PR"
    assert not gh.ran("pr checks")
    assert (
        json.loads(
            (conventions.phase_run_dir(phase_repo, 10) / "ci-peek.json").read_text()
        )["state"]
        == "NO_PR"
    )


@pytest.mark.parametrize(
    "bucket,state", [("pending", "PENDING"), ("fail", "FAILING"), ("pass", "PASSING")]
)
def test_ci_peek_reads_checks_once(phase_repo: Path, bucket: str, state: str) -> None:
    with _RoutedGH(
        {"pr list": _listing({"number": 7, "url": URL}), "pr checks": _checks(bucket)}
    ) as gh:
        out = mode_pr(phase_repo, 10, "peek-ci")
    assert out["state"] == state
    assert len(gh.ran("pr checks")) == 1
    assert "--watch" not in gh.ran("pr checks")[0]


@pytest.mark.parametrize(
    "answer",
    [
        {"returncode": 4, "stdout": "secret"},
        {"stdout": "{}"},
        {"stdout": '[{"url":"bad"}]'},
    ],
)
def test_unreadable_pr_lookup_fails_closed(
    phase_repo: Path, answer: dict[str, object]
) -> None:
    with _RoutedGH({"pr list": answer}) as gh, pytest.raises(ConfigError):
        mode_pr(phase_repo, 10, "open-pr")
    assert not gh.ran("pr create")


def test_lone_ready_retries_then_never_overrides_manual_draft(tmp_path: Path) -> None:
    pub = _receipt_fixture(tmp_path, "lone")
    _write_receipt(pub, 10, pull_request=URL, branch="feature/draft")
    with _RoutedGH({"pr checks": _checks("pending")}) as gh:
        assert mode_ready(pub, 10)["state"] == "HELD"
    assert not gh.ran("pr ready")
    with _RoutedGH({"pr checks": _checks("pass"), "pr ready": {}}) as gh:
        assert mode_ready(pub, 10)["state"] == "READY"
        assert mode_ready(pub, 10)["state"] == "READY"
    assert len(gh.ran("pr ready")) == 1
    assert not gh.ran("pr list")


def test_lone_ready_failure_retries_on_next_tick(tmp_path: Path) -> None:
    pub = _receipt_fixture(tmp_path, "retry")
    _write_receipt(pub, 10, pull_request=URL, branch="feature/draft")
    with _RoutedGH({"pr checks": _checks("pass"), "pr ready": {"returncode": 4}}):
        assert mode_ready(pub, 10)["state"] == "HELD"
    with _RoutedGH({"pr checks": _checks("pass"), "pr ready": {}}) as gh:
        assert mode_ready(pub, 10)["state"] == "READY"
    assert len(gh.ran("pr ready")) == 1
