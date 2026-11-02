# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A closed issue's completed run with no pull request frees itself on the next tick.

The board here is the real fenced store on local Git remotes. Only GitHub is
faked, through the fixture's gh command.
"""

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.board.core import CoordinationError
from argus.board.reservations import ReservationStore
from argus.gitsync.selftest_fixture import _configure_self_test, _init_remote
from argus.issues.claims import save
from argus.issues.client import IssueClient
from argus.issues.source import IssuesSource
from argus.issues.transport import IssuesUnavailable
from issues_fixture import argv, fake_gh, fixture_update, issue, settings

DONE = 100001
NEXT = 100002
KEY = f"{DONE:06d}"
LABELS = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy", "accepted")]
FREE = [{"name": n} for n in ("bug", "area:cli", "difficulty:easy")]
LINE = f"issue {DONE} is closed; completed run released. No action needed."


@dataclass
class Tick:
    root: Path
    github: Path
    store: ReservationStore
    token: dict[str, Any]

    def board(self) -> dict[str, Any]:
        return self.store.read()[0]["phases"]

    def run_folder(self) -> Path:
        return self.root / "vault" / "runs" / f"issue-{KEY}"

    def github_says(self, state: str, labels: list[dict[str, str]] = LABELS) -> None:
        """The search lists open issues only, so a closed one leaves the listing."""
        done = issue(DONE, state=state, labels=labels)
        waiting = issue(NEXT, labels=FREE, createdAt="2025-01-01T12:00:00Z")
        fixture_update(
            self.github,
            rows=[done, waiting] if state == "OPEN" else [waiting],
            views={str(DONE): done},
        )

    def label_writes(self) -> list[list[str]]:
        return [
            line["argv"]
            for line in argv(self.github)
            if "--remove-label" in line["argv"]
        ]

    def views_of_done(self) -> int:
        return sum(
            line["argv"][:3] == ["issue", "view", str(DONE)]
            for line in argv(self.github)
        )


@pytest.fixture
def tick(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Tick]:
    """A claimed issue whose run completed, on a real board, with no pull request."""
    previous = conventions.override({"commands.git": "git"})
    _remote, root = _init_remote(tmp_path, "public")
    _private_remote, seed = _init_remote(tmp_path, "private")
    vault = root / "vault"
    shutil.move(str(seed), vault)
    github = tmp_path / "github"
    github.mkdir()
    binary = fake_gh(github, [], add_cleanup=request.addfinalizer)
    _configure_self_test(root, vault)
    conventions.override(
        {
            **settings(root, binary),
            "driver.runs": vault / "runs",
            "board.repo": vault,
            "board.branch": "fixture-claims",
            "issues.task-dir": vault / "tasks",
        }
    )
    store = ReservationStore(root, issues=True)
    store.migrate_board()
    token = store.reserve(DONE, "worker-one", run_id=f"issue-{KEY}")
    task = vault / "tasks" / f"issue-{KEY}-fix-the-input.md"
    task.parent.mkdir(parents=True)
    task.write_text("frozen task\n", encoding="utf-8")
    save(root, DONE, token, task)
    state = Tick(root, github, store, token)
    (state.run_folder() / "run.json").write_text(
        json.dumps({"state": "completed"}), encoding="utf-8"
    )
    yield state
    conventions.configure(previous)


def select(state: Tick) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = IssuesSource(state.root)
    result = source.select(machine_id="worker-one")
    return result, source.notices


def test_a_held_completed_run_on_a_closed_issue_does_not_block_the_next_issue(
    tick: Tick,
) -> None:
    # Given a closed issue with a held completed run and another open issue, when a
    # tick runs, then the tick claims the other issue and launches nothing for the
    # closed one, so the completed-run exit in the launch is not reached.
    tick.github_says("CLOSED")
    result, _ = select(tick)
    assert result["state"] == "RESERVED", result
    assert result["issue"] == str(NEXT)


@pytest.mark.parametrize("outcome_file", [True, False])
def test_a_closed_issue_releases_the_claim_and_the_label(
    tick: Tick, outcome_file: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a completed run with no pull request, with or without the outcome file,
    # and a person closed the issue, when a tick runs, then the claim and the label
    # go, one line records it, and no Needs You is raised.
    if outcome_file:
        (tick.run_folder() / "no-change.txt").write_text("No change.\n", "utf-8")
    tick.github_says("CLOSED")
    _, notices = select(tick)
    assert KEY not in tick.board()
    assert tick.label_writes() == [
        [
            "issue",
            "edit",
            str(DONE),
            "--repo",
            "example/project",
            "--remove-label",
            "accepted",
        ]
    ]
    assert capsys.readouterr().err.count(LINE) == 1
    assert notices == []


def test_the_release_ends_the_polling_for_that_issue(tick: Tick) -> None:
    # Given a released issue, when the next tick runs, then it reads the issue no
    # more, because the completed run was set aside.
    tick.github_says("CLOSED")
    select(tick)
    assert not (tick.run_folder() / "run.json").exists()
    reads = tick.views_of_done()
    select(tick)
    assert tick.views_of_done() == reads == 1


def test_a_reopened_issue_can_be_claimed_after_the_release(tick: Tick) -> None:
    # Given a released issue, when a person reopens it, then the next tick claims it
    # and nothing from the earlier run traps it.
    tick.github_says("CLOSED")
    select(tick)
    tick.github_says("OPEN", FREE)
    result, _ = select(tick)
    assert result["state"] == "RESERVED", result
    assert result["issue"] == str(DONE)


def test_an_open_issue_holds_the_claim_and_the_label(tick: Tick) -> None:
    # Given a completed run on an issue that stays open, when a tick runs, then the
    # claim, the label, and the run folder stay.
    tick.github_says("OPEN")
    select(tick)
    assert tick.board()[KEY]["status"] == "claimed"
    assert tick.label_writes() == []
    assert (tick.run_folder() / "run.json").exists()


def test_a_failed_issue_read_releases_nothing_and_says_nothing_false(
    tick: Tick,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given the issue state cannot be read, when a tick runs, then the claim, the
    # label, and the run folder stay, and no line says the issue is closed.
    tick.github_says("CLOSED")
    real = IssueClient.view

    def view(
        client: IssueClient, number: int, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if number == DONE:
            raise IssuesUnavailable("gh_failed", "issue view")
        return real(client, number, metadata)

    monkeypatch.setattr(IssueClient, "view", view)
    _, notices = select(tick)
    assert tick.board()[KEY]["status"] == "claimed"
    assert tick.label_writes() == []
    assert (tick.run_folder() / "run.json").exists()
    assert notices == []
    assert "is closed" not in capsys.readouterr().err


def test_an_unknown_issue_state_releases_nothing(tick: Tick) -> None:
    # Given GitHub answers with a state that is neither open nor closed, when a tick
    # runs, then nothing is released.
    tick.github_says("CLOSED")
    fixture_update(
        tick.github, views={str(DONE): issue(DONE, state="UNKNOWN", labels=LABELS)}
    )
    select(tick)
    assert tick.board()[KEY]["status"] == "claimed"
    assert tick.label_writes() == []


def test_a_failed_label_write_keeps_the_claim_and_the_next_tick_retries(
    tick: Tick,
) -> None:
    # Given the label write fails, when a tick runs, then the claim stays, and the
    # next tick finishes the release.
    tick.github_says("CLOSED")
    fixture_update(tick.github, mode="edit-failure")
    select(tick)
    assert tick.board()[KEY]["status"] == "claimed"
    fixture_update(tick.github, mode=None)
    select(tick)
    assert KEY not in tick.board()


def test_a_failed_board_write_keeps_the_run_and_the_next_tick_retries(
    tick: Tick, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the board write fails after the label went, when a tick runs, then the
    # run folder stays, so the next tick finds the claim and releases it.
    tick.github_says("CLOSED")

    def refuse(_store: ReservationStore, _token: dict[str, Any]) -> None:
        raise CoordinationError("board refused")

    with monkeypatch.context() as patch:
        patch.setattr(ReservationStore, "release_group", refuse)
        select(tick)
    assert (tick.run_folder() / "run.json").exists()
    tick.github_says("CLOSED", FREE)
    select(tick)
    assert KEY not in tick.board()
    assert not (tick.run_folder() / "run.json").exists()


def test_an_expired_claim_on_a_closed_issue_gives_back_the_label_once(
    tick: Tick,
) -> None:
    # Given the claim window closed on the completed run, when a person closes the
    # issue and a tick runs, then the label goes, and the next tick reads no more.
    far = "2099-01-01T00:00:00+00:00"
    assert tick.store.expire_stale(now=far)
    tick.github_says("CLOSED")
    select(tick)
    assert len(tick.label_writes()) == 1
    reads = tick.views_of_done()
    select(tick)
    assert tick.views_of_done() == reads


def test_a_pull_request_keeps_its_own_release_path(tick: Tick) -> None:
    # Given a closed pull request on the issue, when a tick runs, then this path
    # changes nothing, and a person still decides through the label.
    tick.github_says("CLOSED")
    pull = {
        "number": 70,
        "html_url": "https://github.com/example/project/pull/70",
        "state": "closed",
        "merged_at": None,
        "draft": False,
        "head": {
            "ref": f"issue/{DONE}-fix-the-input",
            "repo": {"full_name": "example/project"},
            "sha": "a" * 40,
        },
    }
    fixture_update(tick.github, history=[pull])
    select(tick)
    assert tick.board()[KEY]["status"] == "claimed"
    assert tick.label_writes() == []


def test_another_machine_never_releases_the_claim(tick: Tick) -> None:
    # Given the claim belongs to another machine, when this machine ticks, then it
    # reads and writes nothing for that issue.
    tick.github_says("CLOSED")
    source = IssuesSource(tick.root)
    source.select(machine_id="worker-two")
    assert tick.board()[KEY]["machine_id"] == "worker-one"
    assert tick.label_writes() == []
    assert tick.views_of_done() == 0
