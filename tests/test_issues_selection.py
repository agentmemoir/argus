# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Ordering, filter counters, board races, and the selector entry point."""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.source import IssuesSource
from argus.select.next_phase import main
from issues_fixture import Board, argv, fake_gh, fixture_update, issue, settings

Configured = tuple[Path, dict[str, Any], Board]


@pytest.fixture
def configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Configured]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    values = settings(tmp_path, binary)
    previous = conventions.override(values)
    board = Board()

    def store(*_a: object, **_k: object) -> Board:
        return board

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    yield tmp_path, values, board
    conventions.configure(previous)


@pytest.mark.parametrize(
    "preference,winner",
    [
        ("newest", 100003),
        ("easiest", 100001),
        ("hardest", 100002),
        ("category", 100002),
        ("search", 100002),
    ],
)
def test_each_ordering_and_tie_break(
    configured: Configured, preference: str, winner: int
) -> None:
    repo, _, _ = configured
    easy = issue(100001)
    hard = issue(
        100002, labels=[{"name": n} for n in ("bug", "area:store", "difficulty:hard")]
    )
    newest = issue(100003, createdAt="2026-02-01T12:00:00Z")
    fixture_update(repo, rows=[hard, newest, easy])
    conventions.override(
        {
            "issues.preference": preference,
            "issues.category": "area:store" if preference == "category" else None,
            "issues.search": "input" if preference == "search" else None,
        }
    )
    result = IssuesSource(repo).select(reserve=False)
    assert result["issue"] == str(winner)
    assert result["state"] == "AVAILABLE"
    assert not (repo / "tasks").exists()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"state": "CLOSED"}, "not_open"),
        ({"isPullRequest": True}, "is_pull_request"),
        ({"authorAssociation": "NONE"}, "untrusted_author"),
        ({"labels": []}, "missing_labels.type"),
        (
            {
                "labels": [
                    {"name": x}
                    for x in ("bug", "feature", "area:cli", "difficulty:easy")
                ]
            },
            "type_ambiguous",
        ),
        (
            {
                "labels": [
                    {"name": x}
                    for x in ("bug", "area:cli", "difficulty:easy", "difficulty:hard")
                ]
            },
            "difficulty_ambiguous",
        ),
        ({"blockedBy": {"nodes": [{"state": "OPEN"}], "totalCount": 1}}, "blocked"),
    ],
)
def test_structured_filters_count_the_reason(
    configured: Configured, change: dict[str, Any], reason: str
) -> None:
    repo, _, _ = configured
    fixture_update(repo, rows=[issue(**change)])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "NO_PHASE"
    assert result["skipped"][reason] == 1


def test_closed_dependencies_and_many_labels_do_not_block(
    configured: Configured,
) -> None:
    repo, _, _ = configured
    fixture_update(
        repo,
        rows=[
            issue(
                labels=[
                    {"name": x}
                    for x in ("bug", "area:cli", "area:store", "difficulty:easy")
                ],
                blockedBy={"nodes": [{"state": "CLOSED"}], "totalCount": 1},
            )
        ],
    )
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "AVAILABLE" and result["skipped"]["blocked"] == 0


def test_lost_claim_race_tries_the_next_issue(configured: Configured) -> None:
    repo, _, board = configured
    fixture_update(repo, rows=[issue(100001), issue(100002)])
    board.losses = {100001}
    result = IssuesSource(repo).select(store=board)
    assert result["state"] == "RESERVED" and result["issue"] == "100002"
    assert result["skipped"]["claimed"] == 1 and board.claims == [100001, 100002]
    path = repo / result["path"]
    assert path.is_file()
    first = path.read_bytes()
    fixture_update(
        repo,
        rows=[issue(100002, title="Changed after claim", body="changed after claim")],
    )
    result = IssuesSource(repo).select(store=board)
    assert result["state"] == "WAIT_FOR_CLAIM"
    assert path.read_bytes() == first


def test_race_retries_stop_at_configured_bound(configured: Configured) -> None:
    repo, _, board = configured
    fixture_update(repo, rows=[issue(100001), issue(100002), issue(100003)])
    board.losses = {100001, 100002, 100003}
    conventions.override({"issues.claim-attempts": 2})
    result = IssuesSource(repo).select(store=board)
    assert result["state"] == "WAIT_FOR_CLAIM" and result["issue"] == "100002"
    assert result["attempts"] == 2 and board.claims == [100001, 100002]


def test_escalation_skips_until_clear_and_truncation_is_visible(
    configured: Configured,
) -> None:
    repo, _, board = configured
    board.records = {
        "100001": {
            "status": "expired",
            "completion_receipt": {
                "state_reason": "remedy_escalated",
                "cleared": False,
            },
        }
    }
    conventions.override({"issues.list-limit": 1})
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "NO_PHASE" and result["skipped"]["escalated"] == 1
    assert result["truncated"] is True
    board.records["100001"]["completion_receipt"]["cleared"] = True
    assert IssuesSource(repo).select(reserve=False)["state"] == "AVAILABLE"


def test_failed_issue_claim_is_skipped_for_the_next_issue(
    configured: Configured,
) -> None:
    repo, _, board = configured
    fixture_update(repo, rows=[issue(100001), issue(100002)])
    board.records = {
        "100001": {
            "issue": "100001",
            "machine_id": "worker-one",
            "status": "claimed",
            "reservation_id": "rsv-fixture",
            "generation": 1,
            "run_id": "issue-100001",
        }
    }
    run_dir = repo / "runs" / "phase-100001"
    run_dir.mkdir(parents=True)
    (run_dir / "failed.json").write_text(
        json.dumps({"version": 1, "phase": "100001"}), encoding="utf-8"
    )
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "AVAILABLE" and result["issue"] == "100002"
    assert result["skipped"]["failed"] == 1
    assert "100001" in board.records


def test_pending_validated_writeback_retries_before_search(
    configured: Configured,
) -> None:
    repo, _, board = configured
    report = repo / "report.md"
    report.write_text("The input fix is ready for review.", encoding="utf-8")
    receipt = {
        "state_reason": "writeback_pending",
        "body_file": "report.md",
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "validated": True,
    }
    board.records = {
        "100001": {
            "status": "claimed",
            "machine_id": "worker-one",
            "reservation_id": "rsv-fixture",
            "generation": 1,
            "completion_receipt": receipt,
        }
    }
    fixture_update(repo, rows=[])
    result = IssuesSource(repo).select()
    calls = argv(repo)
    assert calls[0]["argv"][0] == "api"
    assert calls[1]["argv"][:2] == ["issue", "comment"]
    # The open pull request read for Needs You notices now follows the search.
    assert ["search", "issues"] in [call["argv"][:2] for call in calls[2:]]
    assert board.records == {} and result["state"] == "NO_PHASE"


def test_changed_writeback_payload_keeps_claim_and_never_launches(
    configured: Configured,
) -> None:
    repo, _, board = configured
    (repo / "report.md").write_text("Changed", encoding="utf-8")
    board.records = {
        "100001": {
            "status": "claimed",
            "machine_id": "worker-one",
            "reservation_id": "rsv-fixture",
            "generation": 1,
            "completion_receipt": {
                "state_reason": "writeback_pending",
                "body_file": "report.md",
                "sha256": "0" * 64,
                "validated": True,
            },
        }
    }
    result = IssuesSource(repo).select()
    assert (
        result["state"] == "WAIT_FOR_CLAIM" and result["status"] == "writeback_pending"
    )
    assert board.records and not (repo / "argv.jsonl").exists()


@pytest.mark.parametrize(
    "flags,expected",
    [
        ([], 0),
        (["--verbose"], 0),
        (["--audit"], 0),
        (["--audit", "--fix-index"], 2),
        (["--server", "--read-only"], 0),
    ],
)
def test_source_methods_through_cli(
    configured: Configured,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    expected: int,
) -> None:
    _, values, _ = configured
    assert main(["--source", "issues", *flags], _settings=values) == expected
    captured = capsys.readouterr()
    if "--server" in flags:
        result = json.loads(captured.out)
        assert result["kind"] == "issue" and result["issue"] == "100001"
    elif "--audit" not in flags:
        assert captured.out.startswith("100001\ttasks/issue-100001-")
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "flags", [[], ["--verbose"], ["--audit"], ["--server", "--read-only"]]
)
def test_every_cli_path_types_remote_failure(
    configured: Configured, capsys: pytest.CaptureFixture[str], flags: list[str]
) -> None:
    repo, values, _ = configured
    fixture_update(repo, mode="failure", error="API rate limit exceeded")
    assert main(["--source", "issues", *flags], _settings=values) == 2
    captured = capsys.readouterr()
    assert "rate_limit" in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_claim_writes_the_board_record_and_the_label_only(
    configured: Configured,
) -> None:
    repo, _, board = configured
    result = IssuesSource(repo).select()
    assert (
        result["state"] == "RESERVED" and board.records["100001"]["status"] == "claimed"
    )
    # Besides the board record, the claim label is the only GitHub write.
    edits = [c["argv"] for c in argv(repo) if c["argv"][:2] == ["issue", "edit"]]
    assert edits == [
        ["issue", "edit", "100001", "--repo", "example/project"]
        + ["--add-label", "accepted"]
    ]
    assert not any(call["argv"][:2] == ["issue", "comment"] for call in argv(repo))
