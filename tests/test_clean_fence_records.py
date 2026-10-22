# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The clean fence lands run state unless another machine holds a live claim.

An escalated phase is ``rejected`` and an abandoned one is released, so the old
fence refused both and their run state stayed dirty for ever; the next publish
then refused on the dirty tree. Each case runs the real ``assert_owner`` and
the real clean rule over board records the case sets, for ``clean <phase>``
and for the whole-tree sweep.
"""

import datetime
from pathlib import Path
from typing import Any

import pytest

from argus.gitsync.fence import phase_fence
from argus.gitsync.mode_clean import mode_clean
from driver_fence_fixture import (
    PHASE,
    RecordedBoard,
    git,
    head_sha,
    make_claim,
    make_repo,
    record,
    run_path,
)

KEY = f"{PHASE:06d}"
_STALE = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)).isoformat()

LANDS: dict[str, dict[str, dict[str, Any]]] = {
    "rejected": {KEY: record(status="rejected")},
    "completed": {KEY: record(status="completed")},
    "accepted": {KEY: record(status="accepted")},
    "released": {},
    "expired": {KEY: record(status="expired")},
    "window-passed": {KEY: record(heartbeat_at=_STALE)},
    "own-live": {KEY: record()},
    "own-later-claim": {KEY: record(reservation_id="rsv-later", generation=3)},
}

REFUSES: dict[str, tuple[dict[str, dict[str, Any]] | None, str]] = {
    "other-live": (
        {KEY: record(machine_id="server-02", reservation_id="rsv-other")},
        "FENCED",
    ),
    "unreachable": (None, "COORDINATION_UNAVAILABLE"),
}


def _dirty_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: dict[str, dict[str, Any]] | None,
) -> Path:
    root = make_repo(tmp_path, monkeypatch)
    make_claim(root, monkeypatch)
    # A bare remote, so a landed clean really commits and pushes.
    git(tmp_path, "init", "-q", "--bare", "remote.git")
    git(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    git(root, "push", "-q", "-u", "origin", "main")
    monkeypatch.setattr("argus.board.reservations.ReservationStore", RecordedBoard)
    monkeypatch.setattr(RecordedBoard, "records", records)
    (run_path(root) / "escalation.json").write_text('{"open": 1}\n')
    return root


@pytest.mark.parametrize("records", LANDS.values(), ids=LANDS.keys())
@pytest.mark.parametrize("sweep", [False, True], ids=["phase", "sweep"])
def test_clean_lands_when_no_other_machine_holds_the_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: dict[str, dict[str, Any]],
    sweep: bool,
) -> None:
    root = _dirty_claim(tmp_path, monkeypatch, records)
    before = head_sha(root)
    out = mode_clean(root) if sweep else mode_clean(root, PHASE)
    assert out["ok"] is True, out["summary"]
    assert "skipped" not in out
    # The summary carries the commit's own error, so a failed land says why.
    assert head_sha(root) != before, str(out["repos"])
    assert git(root, "rev-parse", "origin/main").strip() == head_sha(root)
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize(("records", "state"), REFUSES.values(), ids=REFUSES.keys())
@pytest.mark.parametrize("sweep", [False, True], ids=["phase", "sweep"])
def test_clean_refuses_another_live_claim_or_a_silent_board(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: dict[str, dict[str, Any]] | None,
    state: str,
    sweep: bool,
) -> None:
    root = _dirty_claim(tmp_path, monkeypatch, records)
    before = head_sha(root)
    out = mode_clean(root) if sweep else mode_clean(root, PHASE)
    assert out["ok"] is False
    refusal = out["skipped"][0] if sweep else out
    assert refusal["state"] == state
    assert head_sha(root) == before
    assert (run_path(root) / "escalation.json").exists()


@pytest.mark.parametrize("mode", ["branch", "checkpoint", "tidy", "published"])
def test_other_writers_still_refuse_a_terminal_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = _dirty_claim(tmp_path, monkeypatch, {KEY: record(status="rejected")})
    refusal = phase_fence(root, PHASE, mode) or {}
    assert refusal.get("state") == "FENCED"
    assert "already rejected" in refusal.get("error", "")
