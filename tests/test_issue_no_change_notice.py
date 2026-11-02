# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The no-change notice: the reason reader, its evidence check, and the driver notice."""

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from argus.issues.completed_run import REASON_CAP, checked_no_change_reason

ARGUS = Path(__file__).resolve().parents[1] / "src" / "argus"
LIB = ARGUS / "driver" / "lib-driver-job.sh"
TASK = "issue-315-fix-thing.md"
BRANCH = "issue/315-fix-thing"
OLD_TEXT = (
    "Needs You. Issue #315 finished its run with no pull request. Argus starts no new "
    "run for it. Action: remove the claim label to retry."
)
REASON = "The flag already exists."


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A clone on the issue branch, with no commit past main and a clean tree."""
    origin, seed, work = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-b", "main", str(seed))
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "base")
    _git(seed, "push", str(origin), "main")
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "checkout", "-b", BRANCH)
    return work


def _run_folder(tmp_path: Path, text: str | None) -> Path:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    if text is not None:
        (run / "no-change.txt").write_text(text, encoding="utf-8")
    return run


def _hold(checkout: Path, run: Path, verdict: str, tmp_path: Path) -> list[str]:
    """Run the real issue_completed_hold once; return the notices it sent."""
    notes = tmp_path / "notes.txt"
    driver_run = tmp_path / "driver-run"
    driver_run.mkdir(exist_ok=True)
    script = "\n".join(
        [
            'notify() { printf "%s\\n" "$1" >>"$NOTES"; }',
            "log() { :; }",
            'run_dir_of() { printf "%s" "$RUN_FOLDER"; }',
            'stub_verdict() { [ "$VERDICT" != fail ] || return 1; printf "%s" "$VERDICT"; }',
            "ISSUE_PR_VERDICT_CMD=stub_verdict",
            f'source "{LIB}"',
            f"issue_completed_hold 315 {TASK}",
        ]
    )
    env = {
        **os.environ,
        "NOTES": str(notes),
        "RUN_FOLDER": str(run),
        "VERDICT": verdict,
        "RUN_DIR": str(driver_run),
        "PYTHON": sys.executable,
        "ARGUS": str(ARGUS),
        "REPO": str(checkout),
    }
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
    return notes.read_text(encoding="utf-8").splitlines() if notes.exists() else []


def test_the_reason_is_returned_when_the_evidence_holds(
    checkout: Path, tmp_path: Path
) -> None:
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert checked_no_change_reason(checkout, run, 315, TASK) == REASON


def test_the_reason_is_capped(checkout: Path, tmp_path: Path) -> None:
    run = _run_folder(tmp_path, "x" * 500 + "\n")
    assert (
        checked_no_change_reason(checkout, run, 315, TASK) == "x" * REASON_CAP + "..."
    )


def test_control_characters_become_spaces(checkout: Path, tmp_path: Path) -> None:
    run = _run_folder(tmp_path, "one\x1b[31m two\tthree\nfour\x07\n")
    reason = checked_no_change_reason(checkout, run, 315, TASK)
    assert reason == "one [31m two three four"
    assert all(unicodedata.category(ch)[0] != "C" for ch in reason)


def test_a_missing_file_gives_no_reason(checkout: Path, tmp_path: Path) -> None:
    run = _run_folder(tmp_path, None)
    assert checked_no_change_reason(checkout, run, 315, TASK) == ""


def test_an_unreadable_file_gives_no_reason(checkout: Path, tmp_path: Path) -> None:
    run = _run_folder(tmp_path, None)
    (run / "no-change.txt").mkdir()
    assert checked_no_change_reason(checkout, run, 315, TASK) == ""


def test_a_commit_on_the_branch_refuses_the_reason(
    checkout: Path, tmp_path: Path
) -> None:
    (checkout / "fix.txt").write_text("change\n", encoding="utf-8")
    _git(checkout, "add", "fix.txt")
    _git(checkout, "commit", "-m", "change")
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert checked_no_change_reason(checkout, run, 315, TASK) == ""


def test_a_dirty_tree_refuses_the_reason(checkout: Path, tmp_path: Path) -> None:
    (checkout / "scratch.txt").write_text("left over\n", encoding="utf-8")
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert checked_no_change_reason(checkout, run, 315, TASK) == ""


def test_the_notice_holds_the_reason_when_the_evidence_holds(
    checkout: Path, tmp_path: Path
) -> None:
    run = _run_folder(tmp_path, f"{REASON}\n")
    notices = _hold(checkout, run, "none", tmp_path)
    assert len(notices) == 1
    assert notices[0].startswith(
        "Needs You. Issue #315 ends with no pull request and no code change."
    )
    assert f"did not check it: {REASON}." in notices[0]
    assert notices[0].endswith(
        "Action: close the issue. The next tick then releases the claim. "
        "Check: the claim label is gone. "
        "If the reason is wrong, remove the claim label to retry."
    )


def test_the_notice_is_unchanged_without_the_file(
    checkout: Path, tmp_path: Path
) -> None:
    run = _run_folder(tmp_path, None)
    assert _hold(checkout, run, "none", tmp_path) == [OLD_TEXT]


def test_the_notice_is_unchanged_when_a_commit_refuses_the_file(
    checkout: Path, tmp_path: Path
) -> None:
    (checkout / "fix.txt").write_text("change\n", encoding="utf-8")
    _git(checkout, "add", "fix.txt")
    _git(checkout, "commit", "-m", "change")
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert _hold(checkout, run, "none", tmp_path) == [OLD_TEXT]


def test_the_notice_is_unchanged_when_the_tree_is_dirty(
    checkout: Path, tmp_path: Path
) -> None:
    (checkout / "scratch.txt").write_text("left over\n", encoding="utf-8")
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert _hold(checkout, run, "none", tmp_path) == [OLD_TEXT]


def test_an_unknown_verdict_raises_nothing_even_with_the_file(
    checkout: Path, tmp_path: Path
) -> None:
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert _hold(checkout, run, "fail", tmp_path) == []


def test_a_pull_request_verdict_raises_nothing(checkout: Path, tmp_path: Path) -> None:
    run = _run_folder(tmp_path, f"{REASON}\n")
    assert _hold(checkout, run, "pull_request", tmp_path) == []
