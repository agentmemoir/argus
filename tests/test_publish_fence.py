# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A phase publication writes nothing under a foreign, expired, or unreadable fence.

Two publish writes are covered: the published mode that marks a phase's receipt
(the merge check reaches it through ``--mode published``), and the pull request
the open-pr mode creates for a phase. Each case reads the board through the real
fence over the fixture records, and asserts that the write never happens.
"""

from pathlib import Path
from typing import Any

import pytest

from argus.gitsync.mode_open_pr import mode_open_pr
from argus.gitsync.modes import mode_published
from driver_fence_fixture import (
    PHASE,
    RecordedBoard,
    foreign_claim,
    git,
    make_claim,
    make_repo,
    record,
)

BRANCH = "feature/publish-fence"
PR_URL = "https://example.invalid/pull/1"


def _board_says(monkeypatch: pytest.MonkeyPatch, records: Any) -> None:
    """Point the fence at fixture records; ``None`` is a board that cannot answer."""
    monkeypatch.setattr("argus.board.reservations.ReservationStore", RecordedBoard)
    monkeypatch.setattr(RecordedBoard, "records", records)


def _expired_own_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """This machine's own claim, past its window: the board record says expired."""
    _board_says(monkeypatch, {f"{PHASE:06d}": record(status="expired")})


def _unreadable_board(monkeypatch: pytest.MonkeyPatch) -> None:
    _board_says(monkeypatch, None)


@pytest.fixture
def marks(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Records each receipt write the published mode would make."""
    calls: list[object] = []

    def mark(_root: object, phase: object) -> dict[str, Any]:
        calls.append(phase)
        return {"ok": True, "action": "marked-published"}

    monkeypatch.setattr("argus.gitsync.modes.mark_phase_published", mark)
    return calls


@pytest.fixture
def creates(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stands in for gh: no pull request exists, and every create is recorded."""
    calls: list[list[str]] = []

    def create(_root: object, branch: str, base: str, **_kwargs: object) -> Any:
        calls.append([branch, base])
        return PR_URL, ""

    monkeypatch.setattr(
        "argus.gitsync.mode_open_pr.gh_pull_requests_for_branch",
        lambda _root, _branch: ([], ""),
    )
    monkeypatch.setattr(
        "argus.gitsync.mode_open_pr.gh_create_draft_pull_request", create
    )
    monkeypatch.setattr("argus.gitsync.mode_open_pr.fork_refusal", lambda _root: None)
    monkeypatch.setattr(
        "argus.gitsync.mode_open_pr.unpushed_refusal",
        lambda _root, _branch: None,
    )
    return calls


def _open_pr_for_phase(root: Path) -> dict[str, Any]:
    body = root.parent / "body.md"
    body.write_text("publish the phase\n", encoding="utf-8")
    git(root, "checkout", "-q", "-b", BRANCH)
    return mode_open_pr(root, phase=PHASE, branch=BRANCH, body_file=body, base="main")


def test_published_refuses_a_foreign_fence_before_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marks: list[object]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    out = mode_published(root, PHASE)
    assert out["state"] == "FENCED"
    assert marks == []


def test_published_refuses_an_expired_fence_before_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marks: list[object]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    _expired_own_claim(monkeypatch)
    out = mode_published(root, PHASE)
    assert out["state"] == "FENCED"
    assert marks == []


def test_published_refuses_an_unreadable_board_before_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marks: list[object]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    _unreadable_board(monkeypatch)
    out = mode_published(root, PHASE)
    assert out["state"] == "COORDINATION_UNAVAILABLE"
    assert marks == []


def test_open_pr_refuses_a_foreign_fence_before_the_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, creates: list[list[str]]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    out = _open_pr_for_phase(root)
    assert creates == []
    assert out["state"] == "FENCED"


def test_open_pr_refuses_an_expired_fence_before_the_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, creates: list[list[str]]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    _expired_own_claim(monkeypatch)
    out = _open_pr_for_phase(root)
    assert creates == []
    assert out["state"] == "FENCED"


def test_open_pr_refuses_an_unreadable_board_before_the_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, creates: list[list[str]]
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    _unreadable_board(monkeypatch)
    out = _open_pr_for_phase(root)
    assert creates == []
    assert out["state"] == "COORDINATION_UNAVAILABLE"
