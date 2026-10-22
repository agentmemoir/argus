# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every write path proves the fence before its first write, or writes nothing.

One case per path named by issue 164: ``tidy`` with a phase,
``clean <phase>``, the whole-tree ``clean`` sweep, ``checkpoint``, and
``branch``. A foreign fence must leave the file, ref, and board exactly as they
were and return a refusal; a matching fence must let the write happen. The
``stop`` cases live in ``test_driver_stop_fence.py``.
"""

import contextlib
import json
from pathlib import Path

import pytest

from argus.gitsync.fence import phase_fence
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.mode_clean import mode_checkpoint, mode_clean
from argus.gitsync.modes import mode_tidy
from driver_fence_fixture import (
    PHASE,
    Board,
    foreign_claim,
    git,
    head_sha,
    make_claim,
    make_repo,
    run_path,
)


def _refs(root: Path) -> str:
    return git(root, "for-each-ref", "--format=%(refname) %(objectname)").strip()


# --- tidy -----------------------------------------------------------------


def test_tidy_with_a_phase_refuses_a_foreign_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    before = _refs(root)
    out = mode_tidy(root, PHASE)
    assert out["state"] == "FENCED"
    assert out["ok"] is False
    assert _refs(root) == before


def test_tidy_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    assert phase_fence(root, PHASE, "tidy") is None


def test_tidy_without_a_phase_never_reads_the_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pre-sync call runs before a phase is selected, so it has no fence to
    # read. That call stays exactly as it was; what is asserted absent is the
    # read, not the verdict, which needs a receipt to give.
    read: list[object] = []

    def spy(*args: object) -> None:
        read.append(args)

    monkeypatch.setattr("argus.gitsync.modes.phase_fence", spy)
    with contextlib.suppress(Exception):
        mode_tidy(tmp_path)
    assert read == []


# --- clean ----------------------------------------------------------------


def test_clean_phase_refuses_a_foreign_fence_before_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    before = head_sha(root)
    out = mode_clean(root, PHASE)
    assert out["state"] == "FENCED"
    assert out["repos"] == []
    assert head_sha(root) == before


def test_whole_tree_clean_skips_a_phase_whose_fence_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    before = head_sha(root)
    out = mode_clean(root)
    assert out["state"] == "PARTIAL"
    assert out["skipped"][0]["phase"] == f"{PHASE:06d}"
    assert "fence" in out["skipped"][0]["detail"]
    assert head_sha(root) == before


def test_clean_phase_commits_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    (run_path(root) / "ledger.json").write_text('{"steps": {}}\n')
    out = mode_clean(root, PHASE)
    assert out["ok"] is True
    assert out["repos"][0]["ok"] is True


# --- checkpoint -----------------------------------------------------------


def test_checkpoint_refuses_a_foreign_fence_before_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    git(root, "checkout", "-q", "-b", "feature/x")
    (root / "work.txt").write_text("wip\n")
    before = head_sha(root)
    out = mode_checkpoint(root, phase=PHASE)
    assert out["state"] == "FENCED"
    assert head_sha(root) == before
    assert "?? work.txt" in git(root, "status", "--porcelain")


def test_checkpoint_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    assert phase_fence(root, PHASE, "checkpoint") is None


def test_checkpoint_without_a_phase_never_reads_the_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a phase there is no reservation to prove, so the fence is the
    # caller's; the only production caller names the phase.
    read: list[object] = []

    def spy(*args: object) -> None:
        read.append(args)

    monkeypatch.setattr("argus.gitsync.mode_clean.phase_fence", spy)
    with contextlib.suppress(Exception):
        mode_checkpoint(tmp_path / "not-a-repo")
    assert read == []


# --- branch ---------------------------------------------------------------


def test_branch_refuses_a_foreign_fence_before_the_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    (root / "phases").mkdir()
    (root / "phases" / "phase-100020-cut.md").write_text("# cut\n")
    make_claim(root, monkeypatch)
    foreign_claim(root, monkeypatch)
    before = _refs(root)
    out = mode_branch(root, PHASE, "phase-100020-cut.md", root / "phases")
    assert out["state"] == "FENCED"
    assert out["ok"] is False
    assert _refs(root) == before


def test_branch_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    assert phase_fence(root, PHASE, "branch") is None


# --- the fixture claim the group-resume case was missing -------------------


class _RecordingBoard(Board):
    """The fence board above, plus a claim that names the branch it wrote on."""

    branches: list[str | None] = []

    def __init__(self, *_a: object, branch: str | None = None, **_k: object) -> None:
        self.branches.append(branch)

    def reserve(self, phase: int, machine_id: str) -> dict[str, object]:
        return {
            "phase": f"{int(phase):06d}",
            "machine_id": machine_id,
            "reservation_id": "rsv-fixture",
            "generation": 2,
        }


def test_reserve_phase_writes_the_reservation_the_branch_fence_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claimed phase passes the fence; the same phase with no file refuses.

    The group-resume case cut the group's branch with no claim behind it, so the
    fence refused with COORDINATION_UNAVAILABLE and every later check failed on
    a branch that was never cut.
    """
    from argus.gitsync.selftest_fixture import _reserve_phase

    root = make_repo(tmp_path, monkeypatch)
    _RecordingBoard.branches = []
    monkeypatch.setattr("argus.board.reservations.ReservationStore", _RecordingBoard)
    _reserve_phase(root, 100030, machine_id="argus-selftest", branch="fixture-claims")

    written = run_path(root, 100030) / "reservation.json"
    assert json.loads(written.read_text())["reservation_id"] == "rsv-fixture"
    assert _RecordingBoard.branches == ["fixture-claims"]
    assert phase_fence(root, 100030, "branch") is None
    written.unlink()
    refused = phase_fence(root, 100030, "branch") or {}
    assert refused.get("state") == "COORDINATION_UNAVAILABLE"
    assert refused.get("phase") == "100030"
