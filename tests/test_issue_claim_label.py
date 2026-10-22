# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The claim label: selection skips it, and the board decides a held claim."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from argus import conventions
from argus.issues.source import IssuesSource
from issues_fixture import Board, fake_gh, fixture_update, issue, settings

Configured = tuple[Path, Board]
BASE = ("bug", "area:cli", "difficulty:easy")


def labelled(number: int, *names: str) -> dict[str, Any]:
    # Newer than issue(), so the newest-first order reaches it first.
    return issue(
        number,
        labels=[{"name": n} for n in (*BASE, *names)],
        createdAt="2026-02-01T12:00:00Z",
    )


@pytest.fixture
def configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[Configured]:
    binary = fake_gh(tmp_path, [issue()], add_cleanup=request.addfinalizer)
    previous = conventions.override(settings(tmp_path, binary))
    board = Board()

    def store(*_a: object, **_k: object) -> Board:
        return board

    monkeypatch.setattr("argus.issues.source.ReservationStore", store)
    yield tmp_path, board
    conventions.configure(previous)


def test_an_issue_with_the_claim_label_is_not_picked(configured: Configured) -> None:
    repo, _ = configured
    fixture_update(repo, rows=[labelled(100002, "accepted"), issue(100001)])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "AVAILABLE" and result["issue"] == "100001"
    assert result["skipped"]["claim_label"] == 1


def test_the_label_name_comes_from_config(configured: Configured) -> None:
    repo, _ = configured
    conventions.override({"issues.claim-label": "taken"})
    fixture_update(repo, rows=[labelled(100001, "accepted")])
    assert IssuesSource(repo).select(reserve=False)["issue"] == "100001"
    fixture_update(repo, rows=[labelled(100001, "taken")])
    result = IssuesSource(repo).select(reserve=False)
    assert result["state"] == "NO_PHASE" and result["skipped"]["claim_label"] == 1


def test_a_label_added_after_the_search_stops_the_claim(
    configured: Configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, board = configured
    source = IssuesSource(repo)
    read = source.client.view

    def view(number: int, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        detail = read(number, metadata)
        # A person adds the label between the search and the fresh read.
        detail["labels"].append({"name": "accepted"})
        return detail

    monkeypatch.setattr(source.client, "view", view)
    result = source.select(store=board)
    assert result["state"] == "NO_PHASE" and board.claims == []
    assert result["skipped"]["claim_label"] == 1


@pytest.mark.parametrize(
    ("status", "state"), [("claimed", "WAIT_FOR_CLAIM"), ("expired", "AVAILABLE")]
)
def test_the_board_decides_a_held_or_expired_claim(
    configured: Configured, status: str, state: str
) -> None:
    repo, board = configured
    board.records["100001"] = {"machine_id": "other-worker", "status": status}
    fixture_update(repo, rows=[labelled(100001, "accepted")])
    result = IssuesSource(repo).select(reserve=False, store=board)
    assert result["state"] == state and result["skipped"]["claim_label"] == 0
