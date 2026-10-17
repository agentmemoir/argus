# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One self-test group runs alone, and the bare flag still runs every group."""

import sys
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import main as gitsync_main
from argus.gitsync import selftest

Result = tuple[str, object, str]


def _argv(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    """Point the command at these arguments, so the parser reads them."""
    monkeypatch.setattr(sys, "argv", ["argus.gitsync.main", *args])


def test_the_flag_takes_no_value_or_a_group_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names: list[str] = []

    def fake(name: str) -> int:
        names.append(name)
        return 0

    monkeypatch.setattr(gitsync_main, "self_test_group", fake)
    _argv(monkeypatch, "--self-test")
    assert gitsync_main.main() == 0
    _argv(monkeypatch, "--self-test", "repair")
    assert gitsync_main.main() == 0
    assert names == ["", "repair"]


def test_an_unknown_group_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _argv(monkeypatch, "--self-test", "repiar")
    assert gitsync_main.main() != 0
    err = capsys.readouterr().err
    assert "repiar" in err
    assert "repair" in err


def test_the_repair_group_runs_the_repair_checks_and_no_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[str] = []

    def step(_base: Path) -> list[Result]:
        ran.append("step")
        return [("one", True, "")]

    def scan(_base: Path) -> list[Result]:
        ran.append("scan")
        return [("two", False, "why")]

    def else_where(_base: Path, _checks: list[dict[str, Any]]) -> None:
        ran.append("another group")

    monkeypatch.setattr(selftest, "_repair_step_checks", step)
    monkeypatch.setattr(selftest, "_repair_scan_checks", scan)
    monkeypatch.setattr(selftest, "_sync_one_checks", else_where)
    monkeypatch.setattr(selftest, "make_exec_dir", lambda: str(tmp_path))

    # The selection path itself is what must not drift, so the test names it.
    checks = selftest._self_test_checks(("repair",))  # pyright: ignore[reportPrivateUsage]

    assert ran == ["step", "scan"]
    assert [c["check"] for c in checks] == ["repair: one", "repair scan: two"]
    assert [c["ok"] for c in checks] == [True, False]
