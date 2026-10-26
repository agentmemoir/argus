# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An idle hold does not replace the board's lease and ownership fence."""

import datetime as dt
import shutil
from pathlib import Path

import pytest

from argus import conventions
from argus.board.core import (
    GROUP_COOLDOWN_S,
    GROUP_HEARTBEAT_WINDOW_S,
    GROUP_START_WINDOW_S,
    ReservationConflict,
    ReservationCooldown,
    ReservationFenced,
)
from argus.board.selftest_fixture import _git_test_repo
from argus.board.store import ReservationStore

EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def lease_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReservationStore:
    root = tmp_path / "project"
    root.mkdir()
    git = shutil.which("git")
    assert git is not None
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "repo-root": root,
            "board.repo": tmp_path / "leases-client-1",
            "board.branch": "lease-board",
            "board.remote": "origin",
            "board.file": "coordination/phases.json",
            "commands.git": git,
        },
    )
    _git_test_repo(tmp_path, "leases")
    store = ReservationStore(root)
    store.migrate_board()
    return store


@pytest.mark.parametrize(
    ("status", "window"),
    [
        ("claimed", GROUP_START_WINDOW_S),
        ("paused", GROUP_HEARTBEAT_WINDOW_S),
        ("blocked", GROUP_HEARTBEAT_WINDOW_S),
    ],
)
def test_idle_claim_expires_then_old_fence_cannot_steal_new_owner(
    lease_board: ReservationStore, status: str, window: int
) -> None:
    old = lease_board.reserve_group([100441], "owner-a", status=status, now=EPOCH)[0]
    expiry = EPOCH + dt.timedelta(seconds=window + 1)
    with pytest.raises(ReservationCooldown):
        lease_board.reserve_group([100441], "owner-b", now=expiry)
    with pytest.raises(ReservationFenced):
        lease_board.renew_group(old, now=expiry)
    with pytest.raises(ReservationFenced):
        lease_board.release_group(old, now=expiry)

    available = expiry + dt.timedelta(seconds=GROUP_COOLDOWN_S + 1)
    fresh = lease_board.reserve_group([100441], "owner-b", now=available)[0]
    assert fresh["reservation_id"] != old["reservation_id"]
    with pytest.raises(ReservationFenced):
        lease_board.renew_group(old, now=available)
    with pytest.raises(ReservationConflict):
        lease_board.reserve_group([100441], "owner-a", now=available)
    assert lease_board.assert_owner(fresh)["machine_id"] == "owner-b"


def test_busy_renewal_keeps_work_live_past_original_heartbeat_window(
    lease_board: ReservationStore,
) -> None:
    token = lease_board.reserve_group([100441], "owner-a", status="running", now=EPOCH)[
        0
    ]
    refreshed = EPOCH + dt.timedelta(seconds=GROUP_HEARTBEAT_WINDOW_S - 1)
    lease_board.renew_group(token, status="running", now=refreshed)
    after_original = EPOCH + dt.timedelta(seconds=GROUP_HEARTBEAT_WINDOW_S + 1)
    assert lease_board.expire_stale(now=after_original) == []
    with pytest.raises(ReservationConflict):
        lease_board.reserve_group([100441], "owner-b", now=after_original)
    expired = refreshed + dt.timedelta(seconds=GROUP_HEARTBEAT_WINDOW_S + 1)
    assert [row["phase"] for row in lease_board.expire_stale(now=expired)] == ["100441"]
