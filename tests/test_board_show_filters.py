# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""``show`` narrows the board view to the reservations the caller names."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.cli import _narrow_state, main
from argus.board.selftest_fixture import _git_test_repo
from argus.board.store import ReservationStore


@pytest.fixture
def show_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReservationStore:
    """A real board pair, published through shared settings for the CLI."""
    root = tmp_path / "project"
    root.mkdir()
    git = shutil.which("git")
    assert git is not None
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": root,
            "board.repo": tmp_path / "shows-client-1",
            "board.branch": "show-board",
            "board.remote": "origin",
            "board.file": "coordination/phases.json",
            "commands.git": git,
        },
    )
    # The CLI loads a config file before it reads the board. This fixture
    # publishes the same settings itself, so the load answers without a file.
    monkeypatch.setattr(conventions, "configure_from_file", lambda *_a, **_k: None)
    _git_test_repo(tmp_path, "shows")
    store = ReservationStore(root)
    store.migrate_board()
    return store


@pytest.fixture
def show_board(show_store: ReservationStore) -> dict[str, dict[str, Any]]:
    """One board with two live owners, so a filter has something to leave out."""
    return {
        "a1": show_store.reserve(100001, "machine-a"),
        "a2": show_store.reserve(100002, "machine-a"),
        "b1": show_store.reserve(100003, "machine-b"),
    }


def _show(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    """Run ``show`` with the extra flags and return the JSON it printed."""
    assert main(["show", *args]) == 0
    out = capsys.readouterr().out
    return json.loads(out)


@pytest.mark.usefixtures("show_board")
def test_show_without_filters_prints_the_whole_board(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _show(capsys)
    assert sorted(state["phases"]) == ["100001", "100002", "100003"]


def test_show_narrows_to_one_reservation_id(
    show_board: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    state = _show(capsys, "--reservation-id", show_board["a2"]["reservation_id"])
    assert sorted(state["phases"]) == ["100002"]
    assert state["phases"]["100002"]["machine_id"] == "machine-a"


def test_show_narrows_to_two_reservation_ids_in_one_call(
    show_board: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    state = _show(
        capsys,
        "--reservation-id",
        show_board["a1"]["reservation_id"],
        "--reservation-id",
        show_board["b1"]["reservation_id"],
    )
    assert sorted(state["phases"]) == ["100001", "100003"]


def test_show_omits_an_unknown_reservation_id(
    show_board: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    state = _show(
        capsys,
        "--reservation-id",
        show_board["a1"]["reservation_id"],
        "--reservation-id",
        "release-140-not-on-the-board",
    )
    assert sorted(state["phases"]) == ["100001"]


@pytest.mark.usefixtures("show_board")
def test_show_narrows_to_the_records_one_machine_holds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _show(capsys, "--machine-id", "machine-a")
    assert sorted(state["phases"]) == ["100001", "100002"]


@pytest.mark.usefixtures("show_board")
def test_show_machine_filter_that_matches_nothing_prints_no_records(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _show(capsys, "--machine-id", "machine-c")
    assert state["phases"] == {}


@pytest.mark.parametrize(
    ("reservation", "expected"),
    [("a1", ["100001"]), ("b1", [])],
)
def test_show_applies_the_machine_and_reservation_filters_together(
    show_board: dict[str, dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    reservation: str,
    expected: list[str],
) -> None:
    state = _show(
        capsys,
        "--machine-id",
        "machine-a",
        "--reservation-id",
        show_board[reservation]["reservation_id"],
    )
    assert sorted(state["phases"]) == expected


def test_machine_filter_tolerates_a_record_without_a_machine_id() -> None:
    state: dict[str, Any] = {"version": 2, "phases": {"100001": {"status": "claimed"}}}
    narrowed = _narrow_state(state, machine_id="machine-a")
    assert narrowed["phases"] == {}
