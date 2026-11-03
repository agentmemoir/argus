# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The coordination self-test's checkout hygiene counts only the checkouts it made."""

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board import selftest_hygiene
from argus.board.core import SELF_TEST_BRANCH, WORKTREE_PREFIX

NO_LEFTOVER = "a failed board commit leaves no checkout"


@pytest.fixture(autouse=True)
def _self_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same board settings the self-test sets before its checks run."""
    monkeypatch.setattr(conventions, "_settings", {})
    conventions.configure(
        {
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": Path(),
            "board.file": Path("claims.json"),
            "board.branch": SELF_TEST_BRANCH,
            "board.remote": "origin",
        }
    )


def _act_once_during_check(
    monkeypatch: pytest.MonkeyPatch, action: Callable[[], None]
) -> None:
    """Run ``action`` once, after the check takes its first temp-root snapshot."""
    real_repo = selftest_hygiene._git_test_repo
    pending = [action]

    def repo_after_action(*args: Any, **kwargs: Any) -> Any:
        if pending:
            pending.pop()()
        return real_repo(*args, **kwargs)

    monkeypatch.setattr(selftest_hygiene, "_git_test_repo", repo_after_action)


def _verdict(checks: list[dict[str, Any]], name: str) -> bool:
    (found,) = [check["ok"] for check in checks if check["check"] == name]
    return bool(found)


def _host_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the process temporary folder at a shared root, as a host or driver does."""
    host = tmp_path / "host-temp"
    host.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(host))
    return host


def test_hygiene_check_spares_a_checkout_it_did_not_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_temp(tmp_path, monkeypatch)
    held = Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}other-run" / "checkout"

    def another_run_holds_a_checkout() -> None:
        held.mkdir(parents=True)
        (held / "held.txt").write_text("held by another run\n", encoding="utf-8")

    _act_once_during_check(monkeypatch, another_run_holds_a_checkout)
    base = tmp_path / "base"
    base.mkdir()
    checks: list[dict[str, Any]] = []

    selftest_hygiene._checkout_hygiene_checks(checks, base)

    assert (held / "held.txt").read_text(encoding="utf-8") == "held by another run\n"
    assert _verdict(checks, NO_LEFTOVER) is True


def test_hygiene_check_still_reports_a_leak_from_its_own_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_temp(tmp_path, monkeypatch)

    def own_run_leaks_a_checkout() -> None:
        leak = Path(tempfile.gettempdir()) / f"{WORKTREE_PREFIX}leak" / "checkout"
        leak.mkdir(parents=True)

    _act_once_during_check(monkeypatch, own_run_leaks_a_checkout)
    base = tmp_path / "base"
    base.mkdir()
    checks: list[dict[str, Any]] = []

    selftest_hygiene._checkout_hygiene_checks(checks, base)

    assert _verdict(checks, NO_LEFTOVER) is False, checks
