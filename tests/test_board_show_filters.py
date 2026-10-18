# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""``show`` narrows the board view to the reservations and machine asked for."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.cli import main
from argus.board.git import _git
from argus.board.selftest_fixture import _git_test_repo
from argus.board.store import ReservationStore


def _no_config_file(*_args: object, **_kwargs: object) -> None:
    return None


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
    monkeypatch.setattr(conventions, "configure_from_file", _no_config_file)
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


@pytest.fixture
def live_lock(
    show_board: dict[str, dict[str, Any]], show_store: ReservationStore
) -> None:
    """machine-a holds a live publication lock on the phase it reserved."""
    show_store.acquire_publication(show_board["a1"])


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
    """An empty answer from the filter still has to be a board view.

    The unfiltered read proves the board holds records, and the version key
    proves the narrowed view keeps the board's shape, so an empty phase map
    reads as this machine holding nothing rather than as a view that lost
    the board.
    """
    whole = _show(capsys)
    state = _show(capsys, "--machine-id", "machine-c")
    assert sorted(whole["phases"]) == ["100001", "100002", "100003"]
    assert state["phases"] == {}
    assert state["version"] == whole["version"]


@pytest.mark.usefixtures("show_board")
@pytest.mark.parametrize(
    ("flag", "expected"),
    [("--machine-id", ["100001", "100002"]), ("--reservation-id", ["100002"])],
)
def test_show_ignores_padding_around_an_identifier(
    show_board: dict[str, dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    flag: str,
    expected: list[str],
) -> None:
    """A stray shell space must not read as a machine that holds nothing."""
    asked = (
        "machine-a" if flag == "--machine-id" else show_board["a2"]["reservation_id"]
    )
    state = _show(capsys, flag, f"  {asked}  ")
    assert sorted(state["phases"]) == expected


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


@pytest.mark.usefixtures("live_lock")
def test_show_machine_filter_hides_the_publication_lock_of_another_machine(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _show(capsys, "--machine-id", "machine-b")
    assert sorted(state["phases"]) == ["100003"]
    assert "publication" not in state


@pytest.mark.usefixtures("live_lock")
def test_show_machine_filter_keeps_the_publication_lock_this_machine_holds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _show(capsys, "--machine-id", "machine-a")
    assert sorted(state["phases"]) == ["100001", "100002"]
    assert state["publication"]["machine_id"] == "machine-a"


@pytest.mark.usefixtures("live_lock")
def test_show_reservation_filter_leaves_the_publication_lock_alone(
    show_board: dict[str, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    state = _show(capsys, "--reservation-id", show_board["b1"]["reservation_id"])
    assert sorted(state["phases"]) == ["100003"]
    assert state["publication"]["machine_id"] == "machine-a"


@pytest.mark.usefixtures("show_store")
@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--machine-id", ""),
        ("--machine-id", "   "),
        ("--reservation-id", ""),
        ("--reservation-id", "   "),
    ],
)
def test_show_rejects_an_empty_identifier(
    capsys: pytest.CaptureFixture[str], flag: str, value: str
) -> None:
    assert main(["show", flag, value]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"] == f"{flag} must not be empty"


# Text that a shell, a path lookup, or a line splitter would treat as more than
# an id. Each one is only ever compared to a recorded value, so each one has to
# come back as an id the board does not hold.
_HOSTILE_IDS: dict[str, str] = {
    "command-substitution": "$(id)",
    "backtick": "`id`",
    "shell-separator": "machine-a; rm -rf /",
    "pipe": "machine-a | tee /tmp/stolen",
    "path-traversal": "../../state.json",
    "absolute-path": "/etc/passwd",
    "newline": "machine-a\nmachine-b",
    "very-long": "a" * 4096,
}


@pytest.mark.usefixtures("live_lock")
@pytest.mark.parametrize("hostile", list(_HOSTILE_IDS.values()), ids=list(_HOSTILE_IDS))
def test_show_treats_a_hostile_machine_id_as_an_opaque_id(
    capsys: pytest.CaptureFixture[str], hostile: str
) -> None:
    """A hostile machine id names no machine, and the board read is untouched.

    The unfiltered read holds three phases and a live publication lock, so an
    empty narrowed view means the text matched nothing rather than that the
    board lost its records. The second unfiltered read proves the filter
    narrowed what one read returned and saved nothing back.
    """
    whole = _show(capsys)
    assert sorted(whole["phases"]) == ["100001", "100002", "100003"]
    state = _show(capsys, "--machine-id", hostile)
    assert state["phases"] == {}
    assert "publication" not in state
    assert state["version"] == whole["version"]
    assert _show(capsys) == whole


@pytest.mark.usefixtures("live_lock")
@pytest.mark.parametrize("hostile", list(_HOSTILE_IDS.values()), ids=list(_HOSTILE_IDS))
def test_show_treats_a_hostile_reservation_id_as_an_opaque_id(
    capsys: pytest.CaptureFixture[str], hostile: str
) -> None:
    """A hostile reservation id names no phase, and the board read is untouched.

    ``--reservation-id`` narrows the phase records only, so the live
    publication lock of machine-a has to survive the filter. The second
    unfiltered read proves nothing was written back.
    """
    whole = _show(capsys)
    assert sorted(whole["phases"]) == ["100001", "100002", "100003"]
    state = _show(capsys, "--reservation-id", hostile)
    assert state["phases"] == {}
    assert state["publication"]["machine_id"] == "machine-a"
    assert state["version"] == whole["version"]
    assert _show(capsys) == whole


@pytest.mark.usefixtures("live_lock")
def test_show_refuses_a_blank_reservation_id_before_it_reads_the_board(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A newline and a tab are blank too, and the refusal writes nothing.

    The refusal names the flag, never the supplied text, so the value cannot
    reach the printed view. Reading the whole board after it proves the error
    path left the board as it was.
    """
    whole = _show(capsys)
    assert main(["show", "--reservation-id", " \n\t "]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"] == "--reservation-id must not be empty"
    assert _show(capsys) == whole


@pytest.mark.usefixtures("live_lock")
def test_show_refuses_a_blank_machine_id_before_it_reads_the_board(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A newline and a tab are blank too, and the refusal writes nothing.

    The refusal names the flag, never the supplied text, so the value cannot
    reach the printed view. Reading the whole board after it proves the error
    path left the board as it was.
    """
    whole = _show(capsys)
    assert main(["show", "--machine-id", " \n\t "]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"] == "--machine-id must not be empty"
    assert _show(capsys) == whole


@pytest.mark.usefixtures("show_board")
def test_show_reports_a_failed_board_read_instead_of_an_empty_board(
    show_store: ReservationStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A read that fails is an error, never a board that looks empty.

    The healthy read holds three phases. Once the board branch is gone the same
    command has to report ``COORDINATION_UNAVAILABLE`` with exit 2 and no phase
    map, so a broken read cannot be mistaken for a machine holding nothing.
    """
    whole = _show(capsys)
    assert sorted(whole["phases"]) == ["100001", "100002", "100003"]
    _git(show_store.private, "push", show_store.remote, "--delete", show_store.branch)
    assert main(["show"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["state"] == "COORDINATION_UNAVAILABLE"
    assert "phases" not in printed
