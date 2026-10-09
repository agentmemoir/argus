# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The roadmap self-test: its settings, its dispatcher and its own seams."""

import tempfile
from pathlib import Path

from argus import conventions
from argus.roadmap.select import _gitsync
from argus.roadmap.selftest_board import _board_checks
from argus.roadmap.selftest_evidence import _evidence_checks
from argus.roadmap.selftest_fixture import BOARD_REL, _ReadOnlyStore
from argus.roadmap.selftest_group import _declaration_checks
from argus.roadmap.selftest_selection import _selection_checks
from argus.roadmap.server import RoadmapSource


def self_test():
    conventions.configure(
        {
            "repo-root": Path(),
            "roadmap-dir": Path("roadmap"),
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
    _roadmap_source_checks()
    _self_test_publisher_lookup()


def _roadmap_source_checks():
    # The class the registry hands the driver must reach the same answer as the
    # functions, or the driver is being served by a different code path than
    # the one this file's tests above cover.
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        (repo / "roadmap").mkdir()
        (repo / "roadmap" / "index.md").write_text("Plan ready\n")
        (repo / "roadmap" / "phase-100440-first.md").write_text("x\n")

        source = RoadmapSource(repo, repo / "roadmap")
        got = source.select("server-01", reserve=False, store=_ReadOnlyStore())
        assert got["state"] == "AVAILABLE" and got["phase"] == "100440", got
        assert source.select_next()[0] == 100440
        assert [n for n, _ in source.candidates()] == [100440]
        assert source.signals((100440, repo / "roadmap" / "phase-100440-first.md"))


def _self_test_publisher_lookup():
    from argus.gitsync.review import awaiting_review

    assert _gitsync(Path()) is awaiting_review
