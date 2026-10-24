# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A closed issue's leftover run is released in one line, and never resumed.

The selection tests fake GitHub's view of the issue, because the search list
can still show an issue as open after GitHub has closed it.
"""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.client import IssueClient
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings

MACHINE = "closed-machine"
DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
HARD = [{"name": n} for n in ("bug", "area:store", "difficulty:hard")]
LINE = "issue 100002 is closed; leftover run released"


@pytest.fixture
def configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[tuple[Path, Board]]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    board = Board()
    monkeypatch.setattr("argus.issues.source.ReservationStore", board_store(board))
    yield tmp_path, board
    conventions.configure(previous)


def board_store(board: Board) -> Callable[..., Board]:
    def make(*_args: object, **_kwargs: object) -> Board:
        return board

    return make


def leftover(board: Board) -> None:
    board.records["100002"] = {
        "issue": "100002",
        "machine_id": MACHINE,
        "status": "claimed",
        "reservation_id": "rsv-old",
        "generation": 2,
        "run_id": "issue-100002",
    }


def github_says(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, Any], state: str
) -> None:
    seen = {**row, "state": state}

    def view(
        _self: IssueClient, _number: int, _metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return seen

    monkeypatch.setattr(IssueClient, "view", view)


def test_a_leftover_claim_on_a_closed_issue_is_released_and_not_selected(
    configured: tuple[Path, Board],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, board = configured
    row = issue(100002, labels=HARD, state="OPEN")
    fixture_update(repo, rows=[row])
    leftover(board)
    github_says(monkeypatch, row, "CLOSED")
    result = IssuesSource(repo).select(machine_id=MACHINE, store=board)
    assert result["state"] == "NO_PHASE", result
    assert board.released == ["100002"], board.released
    assert LINE in capsys.readouterr().err


def test_a_closed_issue_is_never_reserved_from_a_stale_search(
    configured: tuple[Path, Board], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A guard: this already holds on main, because the detail check skips it.
    repo, board = configured
    row = issue(100002, labels=HARD, state="OPEN")
    fixture_update(repo, rows=[row])
    github_says(monkeypatch, row, "CLOSED")
    result = IssuesSource(repo).select(machine_id=MACHINE, store=board)
    assert result["state"] == "NO_PHASE", result
    assert board.claims == [] and board.released == []


def test_release_if_closed_releases_only_a_closed_issue(
    configured: tuple[Path, Board],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from argus.issues.source import release_if_closed

    repo, board = configured
    row = issue(100002, labels=HARD, state="OPEN")
    leftover(board)
    github_says(monkeypatch, row, "OPEN")
    assert release_if_closed(repo, 100002, MACHINE) is False
    assert board.released == [] and "100002" in board.records
    github_says(monkeypatch, row, "CLOSED")
    assert release_if_closed(repo, 100002, MACHINE) is True
    assert board.released == ["100002"]
    assert LINE in capsys.readouterr().err


def run_bash(
    tmp_path: Path, script: str, **env: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TEST_DRIVER": str(DRIVER),
            "TEST_ROOT": str(tmp_path),
            "TEST_PYTHON": sys.executable,
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


PRELUDE = """
for name in lib-driver-log lib-driver-run lib-driver-halt-record lib-driver-resume; do
  . "$TEST_DRIVER/$name.sh"
done
PYTHON="$TEST_PYTHON"; ARGUS="$TEST_ROOT"; REPO="$TEST_ROOT"; ARGUS_CONFIG_ARGS=()
RUN_DIR="$TEST_ROOT/run"; mkdir -p "$RUN_DIR"
HALT="$RUN_DIR/halted"; HALT_RECORD="$RUN_DIR/halted.json"; : >"$HALT_RECORD"
work_kind=issue; PHASE_FILE_INPUT=issue_file
halt_record_read() {
  HALT_REC_PHASE=270; HALT_REC_PHASE_FILE=""; HALT_REC_RUN_DIR=""
  HALT_REC_STATE=""; HALT_REC_RESERVATION_ID=""; HALT_REC_GENERATION=""
  return 0
}
"""

# The stub mirrors the error the real phase_run_dir raised when an issue had no
# task file: a traceback on stderr. A fixed driver never calls it.
RESUME = (
    PRELUDE
    + """
issue_task_file_of() { return 0; }
phase_run_dir() {
  if [ -z "${2:-}" ]; then
    printf 'Traceback (most recent call last):\\nValueError: missing required input issue_file\\n' >&2
    return 1
  fi
  printf '%s\\n' "$RUN_DIR"
}
work_preflight() { return 0; }
_drive_resume_group() { return 1; }
resume_refuse() { printf 'refused: %s\\n' "$2" >&2; return 1; }
_drive_resume "" ; echo "rc=$?"
"""
)

RELEASE = (
    PRELUDE
    + """
issue_leftover_check() { return "$TEST_CHECK_RC"; }
issue_leftover_release; echo "rc=$?"
if [ -e "$HALT_RECORD" ]; then echo kept; else echo removed; fi
"""
)


def test_a_resume_of_an_issue_without_a_task_file_refuses_in_one_line(
    tmp_path: Path,
) -> None:
    result = run_bash(tmp_path, RESUME)
    assert "rc=1" in result.stdout, result
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "issue 270 has no task file; the pipeline input issue_file is missing; "
        "nothing resumed"
    ) in result.stderr, result.stderr


@pytest.mark.parametrize(
    ("check", "rc", "record"),
    [("0", "rc=0", "removed"), ("1", "rc=1", "kept"), ("2", "rc=2", "kept")],
)
def test_a_leftover_record_is_removed_only_for_a_closed_issue(
    tmp_path: Path, check: str, rc: str, record: str
) -> None:
    result = run_bash(tmp_path, RELEASE, TEST_CHECK_RC=check)
    assert result.stdout.splitlines() == [rc, record], result
