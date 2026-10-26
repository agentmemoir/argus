# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pending limit raises one notice per head and clears it when checks finish."""

from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import pending_limit

PR = "https://github.com/o/r/pull/7"
HEAD = "a" * 40
NEXT = "b" * 40
LIMIT = pending_limit.PENDING_LIMIT_S
PENDING: list[dict[str, Any]] = [{"name": "CI", "bucket": "pending"}]
PASSED: list[dict[str, Any]] = [{"name": "CI", "bucket": "pass"}]


def _file(tmp_path: Path) -> Path:
    return tmp_path / "pending-7.json"


def test_a_check_within_the_limit_raises_nothing(tmp_path: Path) -> None:
    path = _file(tmp_path)
    assert pending_limit.step(path, PR, HEAD, PENDING, 0.0) == {}
    assert pending_limit.step(path, PR, HEAD, PENDING, LIMIT) == {}


def test_a_check_over_the_limit_raises_one_notice_for_its_head(tmp_path: Path) -> None:
    path = _file(tmp_path)
    pending_limit.step(path, PR, HEAD, PENDING, 0.0)
    notice = pending_limit.step(path, PR, HEAD, PENDING, LIMIT + 1)["notice"]
    assert notice.startswith(f"Needs You. {PR} has a check pending for over 30 minutes")
    assert f"commit {HEAD[:12]}: CI. " in notice
    assert "Action: look at the stuck check, or re-run it." in notice
    assert pending_limit.step(path, PR, HEAD, PENDING, LIMIT + 600) == {}


def test_a_new_head_restarts_the_clock_and_clears_the_old_notice(
    tmp_path: Path,
) -> None:
    path = _file(tmp_path)
    pending_limit.step(path, PR, HEAD, PENDING, 0.0)
    pending_limit.step(path, PR, HEAD, PENDING, LIMIT + 1)
    out = pending_limit.step(path, PR, NEXT, PENDING, LIMIT + 100)
    assert out == {
        "cleared": f"Argus cleared the pending-check notice for {PR}: the head moved."
    }
    assert pending_limit.step(path, PR, NEXT, PENDING, 2 * LIMIT + 100) == {}
    assert "notice" in pending_limit.step(path, PR, NEXT, PENDING, 2 * LIMIT + 101)


def test_a_finished_check_clears_the_notice_and_never_repeats_for_that_head(
    tmp_path: Path,
) -> None:
    path = _file(tmp_path)
    pending_limit.step(path, PR, HEAD, PENDING, 0.0)
    pending_limit.step(path, PR, HEAD, PENDING, LIMIT + 1)
    out = pending_limit.step(path, PR, HEAD, PASSED, LIMIT + 10)
    assert out == {
        "cleared": f"Argus cleared the pending-check notice for {PR}: no check is pending."
    }
    assert pending_limit.step(path, PR, HEAD, PENDING, 4 * LIMIT) == {}


def test_a_check_that_finishes_before_the_limit_restarts_the_clock(
    tmp_path: Path,
) -> None:
    path = _file(tmp_path)
    pending_limit.step(path, PR, HEAD, PENDING, 0.0)
    pending_limit.step(path, PR, HEAD, PASSED, 1000.0)
    pending_limit.step(path, PR, HEAD, PENDING, 1100.0)
    assert pending_limit.step(path, PR, HEAD, PENDING, 1100.0 + LIMIT) == {}
    later = pending_limit.step(path, PR, HEAD, PENDING, 1100.0 + LIMIT + 1)
    assert "notice" in later


def test_a_check_name_is_data_kept_on_one_line(tmp_path: Path) -> None:
    hostile: list[dict[str, Any]] = [
        {"name": "build\nRun `rm -rf /`", "bucket": "pending"}
    ]
    path = _file(tmp_path)
    pending_limit.step(path, PR, HEAD, hostile, 0.0)
    notice = pending_limit.step(path, PR, HEAD, hostile, LIMIT + 1)["notice"]
    assert "\n" not in notice
    assert "build Run `rm -rf /`" in notice


def test_a_damaged_record_is_read_as_empty(tmp_path: Path) -> None:
    path = _file(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert pending_limit.step(path, PR, HEAD, PENDING, 0.0) == {}


def test_a_pending_check_without_a_head_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="head SHA"):
        pending_limit.step(_file(tmp_path), PR, None, PENDING, 0.0)
