# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A finished or expired claim waits for its label, then starts one clean run.

A claim that ended with a closed pull request, with a completed run and no pull
request, or with an expired claim and no pull request keeps its issue held while
the claim label stays. Argus raises one Needs You line for it. Once a person
removes the label, the next tick retires this machine's finished record, sets the
earlier run folder aside, and claims the issue again.
"""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings

ELIGIBLE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
LABELLED = [*ELIGIBLE, {"name": "accepted"}]
BASE = "issue/100001-fix-the-input"
DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"


def record(status: str) -> dict[str, Any]:
    return {
        "issue": "100001",
        "machine_id": "worker-one",
        "status": status,
        "reservation_id": "rsv-own",
        "generation": 1,
        "run_id": "issue-100001",
    }


def closed_pull(number: int, branch: str) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.com/example/project/pull/{number}",
        "state": "closed",
        "merged_at": None,
        "draft": False,
        "head": {
            "ref": branch,
            "repo": {"full_name": "example/project"},
            "sha": "a" * 40,
        },
    }


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Board:
    shared = Board()

    def store(*_a: object, **_k: object) -> Board:
        return shared

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    return shared


@pytest.fixture
def repo(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    binary = fake_gh(
        tmp_path, [issue(labels=ELIGIBLE)], add_cleanup=request.addfinalizer
    )
    previous = conventions.override(settings(tmp_path, binary))
    yield tmp_path
    conventions.configure(previous)


def completed_run(repo: Path) -> Path:
    """The earlier run's folder, as glide leaves a completed run."""
    folder = repo / "runs" / "issue-100001"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text('{"state": "completed"}\n', encoding="utf-8")
    return folder


def set_aside(repo: Path) -> list[Path]:
    return sorted((repo / "runs").glob("issue-100001.prev-*"))


def tick(repo: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = IssuesSource(repo)
    result = source.select()
    return result, source.notices


def test_a_closed_pull_request_and_no_label_start_a_clean_run_on_a_new_branch(
    repo: Path, board: Board
) -> None:
    # Given a completed run whose Argus pull request closed without merge, and no
    # claim label, when a tick runs, then the record is retired, the earlier run
    # folder is set aside, and the new claim on -2 finds no completed run.
    board.records["100001"] = record("completed")
    folder = completed_run(repo)
    fixture_update(repo, history=[closed_pull(70, BASE)])
    result, _ = tick(repo)
    assert result["state"] == "RESERVED", result
    assert Path(result["path"]).name == "issue-100001-fix-the-input-2.md"
    assert board.retired == ["100001"]
    assert not (folder / "run.json").exists()
    assert [(old / "run.json").is_file() for old in set_aside(repo)] == [True]


def test_a_completed_run_with_no_pull_request_asks_for_the_label_then_retries(
    repo: Path, board: Board
) -> None:
    # Given a completed run with no pull request and the claim label, when two
    # ticks run, then each raises the same one line and nothing is claimed. When
    # the label is removed, the record is retired once and the issue is claimed.
    board.records["100001"] = record("completed")
    folder = completed_run(repo)
    fixture_update(repo, rows=[issue(labels=LABELLED)])
    first, notices = tick(repo)
    second, again = tick(repo)
    assert first["state"] == second["state"] == "NO_PHASE"
    assert notices == again
    assert [n["key"] for n in notices] == ["completed-100001"]
    assert notices[0]["text"].endswith("Action: remove the claim label to retry.")
    assert board.retired == [] and board.claims == []
    fixture_update(repo, rows=[issue(labels=ELIGIBLE)])
    third, _ = tick(repo)
    fourth, _ = tick(repo)
    assert third["state"] == "RESERVED", third
    assert Path(third["path"]).name == "issue-100001-fix-the-input.md"
    assert fourth["state"] == "WAIT_FOR_CLAIM", fourth
    assert board.retired == ["100001"]
    assert not (folder / "run.json").exists() and len(set_aside(repo)) == 1


def test_an_expired_claim_with_no_pull_request_asks_for_the_label_then_retries(
    repo: Path, board: Board
) -> None:
    # Given an expired claim with the claim label and no pull request, when two
    # ticks run, then each raises the same one line. When the label is removed,
    # the earlier run is set aside and the issue is claimed again.
    board.records["100001"] = record("expired")
    folder = completed_run(repo)
    fixture_update(repo, rows=[issue(labels=LABELLED)])
    _, notices = tick(repo)
    _, again = tick(repo)
    assert notices == again
    assert [n["key"] for n in notices] == ["expired-100001"]
    assert notices[0]["text"].endswith("Action: remove the claim label to retry.")
    assert board.claims == []
    fixture_update(repo, rows=[issue(labels=ELIGIBLE)])
    result, _ = tick(repo)
    assert result["state"] == "RESERVED", result
    assert board.retired == []
    assert not (folder / "run.json").exists() and len(set_aside(repo)) == 1


@pytest.mark.usefixtures("board")
def test_a_label_a_person_added_raises_no_notice(repo: Path) -> None:
    # Given no board record, a claim label is a person's hold, and Argus says nothing.
    fixture_update(repo, rows=[issue(labels=LABELLED)])
    _, notices = tick(repo)
    assert notices == []


def report_twice(tmp_path: Path, selection: str) -> list[str]:
    """Run the driver's notice step on two ticks; return each line sent."""
    script = """
. "$TEST_DRIVER/lib-driver-select-real.sh"
PYTHON="$TEST_PYTHON"; RUN_DIR="$TEST_ROOT/state"; mkdir -p "$RUN_DIR"
notify() { printf '%s\\n' "$1" >>"$TEST_ROOT/sent"; }
report_issue_notices "$TEST_SELECTION"
report_issue_notices "$TEST_SELECTION"
"""
    subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            "TEST_SELECTION": selection,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=True,
    )
    sent = tmp_path / "sent"
    return sent.read_text(encoding="utf-8").splitlines() if sent.exists() else []


def test_two_closed_pull_requests_raise_one_line_once_over_two_ticks(
    repo: Path, board: Board
) -> None:
    # Given two Argus pull requests that closed without merge and the claim label,
    # when the driver reports two ticks, then one line names both and is sent once.
    board.records["100001"] = record("completed")
    fixture_update(
        repo,
        rows=[issue(labels=LABELLED)],
        history=[closed_pull(70, BASE), closed_pull(71, f"{BASE}-2")],
    )
    _, notices = tick(repo)
    sent = report_twice(repo, json.dumps({"notices": notices}))
    assert len(sent) == 1, sent
    assert "pull/70" in sent[0] and "pull/71" in sent[0]
