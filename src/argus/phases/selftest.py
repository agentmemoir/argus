# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The phases self-test: its settings, its dispatcher and its own seams."""

import tempfile
from pathlib import Path

from argus import conventions
from argus.board.git import isolate_self_test_git
from argus.phases.select import _gitsync
from argus.phases.selftest_board import _board_checks
from argus.phases.selftest_evidence import _evidence_checks
from argus.phases.selftest_fixture import BOARD_REL, _ReadOnlyStore
from argus.phases.selftest_group import _declaration_checks
from argus.phases.selftest_selection import _selection_checks
from argus.phases.server import PhasesSource


def self_test() -> None:
    # The board checks below build scratch repositories and run git in them, and
    # they must answer to the fixture alone: a host global `core.hooksPath`,
    # signing key, template, or alias must not change how long this takes or what
    # it decides. Set before the first check, so nothing can run git first.
    isolate_self_test_git()
    conventions.configure(
        {
            "repo-root": Path(),
            "phases-dir": Path("phases"),
            "driver.runs": Path("runs"),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "pipeline": None,
            "machine.id": "self-test",
            "board.repo": BOARD_REL,
            "board.file": Path("claims.json"),
            "board.branch": "fixture-claims",
            "board.remote": "origin",
            "commands.git": "git",
            "commands.gh": "gh",
        }
    )
    _declaration_checks()
    _evidence_checks()
    _selection_checks()
    _board_checks()
    _phases_source_checks()
    _self_test_publisher_lookup()


def _phases_source_checks() -> None:
    # The class the registry hands the driver must reach the same answer as the
    # functions, or the driver is being served by a different code path than
    # the one this file's tests above cover.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "phases").mkdir()
        (repo / "phases" / "index.md").write_text("Plan ready\n")
        (repo / "phases" / "phase-100440-first.md").write_text("x\n")

        source = PhasesSource(repo, repo / "phases")
        got = source.select("server-01", reserve=False, store=_ReadOnlyStore())
        assert got["state"] == "AVAILABLE" and got["phase"] == "100440", got
        next_phase = source.select_next()
        assert next_phase is not None and next_phase[0] == 100440, next_phase
        assert [n for n, _ in source.candidates()] == [100440]
        assert source.signals((100440, repo / "phases" / "phase-100440-first.md"))


def _self_test_publisher_lookup() -> None:
    from argus.gitsync.review import awaiting_review

    assert _gitsync(Path()) is awaiting_review
