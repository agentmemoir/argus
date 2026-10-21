# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every write path proves the fence before its first write, or writes nothing.

One case per path named by issue 164: ``stop``, ``tidy`` with a phase,
``clean <phase>``, the whole-tree ``clean`` sweep, ``checkpoint``, and
``branch``. A foreign fence must leave the file, ref, and board exactly as they
were and return a refusal; a matching fence must let the write happen.
"""

import contextlib
import json
import subprocess
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import ReservationFenced
from argus.gitsync.fence import phase_fence
from argus.gitsync.mode_branch import mode_branch
from argus.gitsync.mode_clean import mode_checkpoint, mode_clean
from argus.gitsync.modes import mode_tidy
from driver_maintenance_support import shell

PHASE = 100020


def _run(root: Path, phase: int = PHASE) -> Path:
    return root / "runs" / f"phase-{phase}"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout with one commit, and the settings every mode reads."""
    monkeypatch.setattr(conventions, "_settings", {})
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "README").write_text("fixture\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    conventions.configure(
        {"commands.git": "git", "repo-root": str(root), "driver.runs": "runs"}
    )
    return root


class _Board:
    """A board that confirms the fixture reservation and fences every other one.

    The board transport is proved by ``tests/test_driver_claim_fencing.py`` and
    the board's own suite; what these cases pin is the ordering, so the fence is
    stubbed at its one hole rather than rebuilt with a bare-clone board here.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def assert_owner(self, reservation: dict[str, object]) -> dict[str, object]:
        if reservation.get("reservation_id") != "rsv-fixture":
            raise ReservationFenced(f"{PHASE} is no longer owned by this reservation")
        return dict(reservation)


def _claim(root: Path, monkeypatch: pytest.MonkeyPatch, phase: int = PHASE) -> None:
    """The run state a live reservation leaves behind, and a board that owns it."""
    monkeypatch.setattr("argus.board.reservations.ReservationStore", _Board)
    run = _run(root, phase)
    run.mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text('{"state": "running"}\n')
    (run / "reservation.json").write_text(
        json.dumps(
            {
                "machine_id": "server-01",
                "reservation_id": "rsv-fixture",
                "generation": 2,
                "phase": f"{phase:06d}",
            }
        )
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "run state")


def _foreign(root: Path, phase: int = PHASE) -> None:
    """A reservation file whose id no board record carries."""
    path = _run(root, phase) / "reservation.json"
    data = json.loads(path.read_text())
    data["reservation_id"] = "rsv-foreign"
    path.write_text(json.dumps(data))


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def _refs(root: Path) -> str:
    return _git(root, "for-each-ref", "--format=%(refname) %(objectname)").strip()


# --- stop -----------------------------------------------------------------


def test_stop_refuses_a_foreign_fence_and_writes_neither_file(
    tmp_path: Path,
) -> None:
    output = shell(
        tmp_path,
        "printf '100020\\trsv-foreign\\t7\\n' >\"$CLAIM\"\n"
        "board_assert_owner() { log ASSERT; return 1; }\n"
        "rc=0; stop || rc=$?\n"
        'printf "stop_rc=%s\\n" "$rc"\n',
    )
    assert "stop_rc=1" in output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert not (tmp_path / "halted").exists()
    assert not (tmp_path / "stopped-claim").exists()


def test_stop_without_a_claim_writes_neither_file(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "board_assert_owner() { log ASSERT; return 0; }\n"
        "rc=0; stop || rc=$?\n"
        'printf "stop_rc=%s\\n" "$rc"\n',
    )
    assert "stop_rc=1" in output
    assert "ASSERT" not in (tmp_path / "events").read_text().splitlines()
    assert not (tmp_path / "halted").exists()
    assert not (tmp_path / "stopped-claim").exists()


def test_stop_writes_both_files_when_the_fence_matches(tmp_path: Path) -> None:
    output = shell(
        tmp_path,
        "printf '100020\\trsv-fixture\\t2\\n' >\"$CLAIM\"\n"
        "board_assert_owner() { log ASSERT; return 0; }\n"
        "rc=0; stop || rc=$?\n"
        'printf "stop_rc=%s\\n" "$rc"\n',
    )
    assert "stop_rc=0" in output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert (tmp_path / "halted").is_file()
    assert (tmp_path / "stopped-claim").read_text().strip() == "100020"


# --- tidy -----------------------------------------------------------------


def test_tidy_with_a_phase_refuses_a_foreign_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    _foreign(root)
    before = _refs(root)
    out = mode_tidy(root, PHASE)
    assert out["state"] == "FENCED"
    assert out["ok"] is False
    assert _refs(root) == before


def test_tidy_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
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
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    _foreign(root)
    before = _head(root)
    out = mode_clean(root, PHASE)
    assert out["state"] == "FENCED"
    assert out["repos"] == []
    assert _head(root) == before


def test_whole_tree_clean_skips_a_phase_whose_fence_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    _foreign(root)
    before = _head(root)
    out = mode_clean(root)
    assert out["state"] == "PARTIAL"
    assert out["skipped"][0]["phase"] == f"{PHASE:06d}"
    assert "fence" in out["skipped"][0]["detail"]
    assert _head(root) == before


def test_clean_phase_commits_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    (_run(root) / "ledger.json").write_text('{"steps": {}}\n')
    out = mode_clean(root, PHASE)
    assert out["ok"] is True
    assert out["repos"][0]["ok"] is True


# --- checkpoint -----------------------------------------------------------


def test_checkpoint_refuses_a_foreign_fence_before_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    _foreign(root)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "work.txt").write_text("wip\n")
    before = _head(root)
    out = mode_checkpoint(root, phase=PHASE)
    assert out["state"] == "FENCED"
    assert _head(root) == before
    assert "?? work.txt" in _git(root, "status", "--porcelain")


def test_checkpoint_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
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
    root = _repo(tmp_path, monkeypatch)
    (root / "phases").mkdir()
    (root / "phases" / "phase-100020-cut.md").write_text("# cut\n")
    _claim(root, monkeypatch)
    _foreign(root)
    before = _refs(root)
    out = mode_branch(root, PHASE, "phase-100020-cut.md", root / "phases")
    assert out["state"] == "FENCED"
    assert out["ok"] is False
    assert _refs(root) == before


def test_branch_fence_passes_when_the_fence_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, monkeypatch)
    _claim(root, monkeypatch)
    assert phase_fence(root, PHASE, "branch") is None
