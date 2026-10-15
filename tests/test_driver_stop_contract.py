# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Stop holds future ticks; an overlapping explicit resume may accept a stop."""

import datetime as dt
import json
from pathlib import Path

import pytest

from argus.board.core import GROUP_COOLDOWN_S, GROUP_HEARTBEAT_WINDOW_S
from argus.board.store import ReservationStore
from driver_control_fixture import blocked_run, control_tmux, resume_barrier
from driver_publication_fixture import (
    PHASE,
    DriverPublication,
    driver_publication_fixture,
)

__all__ = ["driver_publication_fixture"]


@pytest.mark.parametrize("boundary", ["before-consume", "after-consume"])
def test_overlapping_stop_and_explicit_resume_keep_line_halted(
    driver_publication: DriverPublication, exec_dir: Path, boundary: str
) -> None:
    fixture = driver_publication
    blocked_run(fixture)
    stopped = fixture.tick("--stop")
    assert stopped.returncode == 0, stopped.stderr
    with resume_barrier(fixture, exec_dir, boundary) as (process, gate):
        stopped = fixture.tick("--stop")
        assert stopped.returncode == 0, stopped.stderr
        assert (fixture.state / "stopped-claim").read_text().strip() == str(PHASE)
        gate.sendall(b"1")
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, (out, err)
    assert (fixture.state / "halted").exists()
    assert (fixture.state / "transport-window").exists()
    hold = fixture.state / "stopped-claim"
    # Overlapping calls can accept the stop before launch. Once the accepted
    # hold is consumed, a later stop must remain for the next idle tick.
    assert hold.exists() == (boundary == "after-consume")
    if boundary == "after-consume":
        (fixture.state / "transport-window").unlink()
        (fixture.state / "halted").unlink()
        before = fixture.commands()
        tick = fixture.tick()
        assert tick.returncode == 0, tick.stderr
        assert not (fixture.state / "transport-window").exists()
        assert hold.read_text().strip() == str(PHASE)
        assert len([c for c in fixture.commands() if c[1] == "new-window"]) == len(
            [c for c in before if c[1] == "new-window"]
        )


def test_idle_hold_does_not_renew_but_busy_work_renews_behind_halt(
    driver_publication: DriverPublication, exec_dir: Path
) -> None:
    fixture = driver_publication
    control_tmux(fixture, exec_dir)
    store = ReservationStore(fixture.root)
    before, _ = store.read()
    stopped = fixture.tick("--stop")
    assert stopped.returncode == 0, stopped.stderr
    idle = fixture.tick()
    assert idle.returncode == 0, idle.stderr
    assert store.read()[0] == before
    assert not (fixture.state / f"claim-renew-{PHASE}.json").exists()
    (fixture.state / "transport-window").touch()
    busy = fixture.tick()
    assert busy.returncode == 0, busy.stderr
    assert (fixture.state / f"claim-renew-{PHASE}.json").exists()
    assert store.read()[0]["phases"][str(PHASE)]["status"] == "running"


def test_stop_during_tick_launch_holds_later_idle_ticks(
    driver_publication: DriverPublication, exec_dir: Path
) -> None:
    fixture = driver_publication
    blocked_run(fixture)
    config = json.loads(fixture.config.read_text())
    config["driver"]["recover-own-claim"] = True
    fixture.config.write_text(json.dumps(config))
    (fixture.state / "halted").unlink()
    with resume_barrier(fixture, exec_dir, "after-consume", modes=()) as (
        process,
        gate,
    ):
        stopped = fixture.tick("--stop")
        assert stopped.returncode == 0, stopped.stderr
        gate.sendall(b"1")
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, (out, err)
    assert (fixture.state / "halted").exists()
    assert (fixture.state / "stopped-claim").read_text().strip() == str(PHASE)
    (fixture.state / "transport-window").unlink()
    (fixture.state / "halted").unlink()
    assert fixture.tick().returncode == 0
    assert not (fixture.state / "transport-window").exists()
    assert (fixture.state / "stopped-claim").exists()


def test_release_returns_manually_stopped_claim_without_clearing_halt(
    driver_publication: DriverPublication, exec_dir: Path
) -> None:
    fixture = driver_publication
    control_tmux(fixture, exec_dir)
    assert fixture.tick("--stop").returncode == 0
    released = fixture.tick("--release")
    assert released.returncode == 0, released.stderr
    assert (fixture.state / "halted").exists()
    assert not (fixture.state / "stopped-claim").exists()
    assert not (fixture.state / "claim").exists()
    assert str(PHASE) not in ReservationStore(fixture.root).read()[0]["phases"]


def test_expired_resume_reacquires_free_slot_but_cannot_take_rivals_claim(
    driver_publication: DriverPublication, exec_dir: Path
) -> None:
    fixture = driver_publication
    blocked_run(fixture)
    control_tmux(fixture, exec_dir)
    store = ReservationStore(fixture.root)
    store.release_group(fixture.token)
    epoch = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    old = store.reserve_group([PHASE], "server-01", status="paused", now=epoch)[0]
    claim = fixture.state / "claim"
    claim.write_text(f"{PHASE}\t{old['reservation_id']}\t{old['generation']}\n")
    expiry = epoch + dt.timedelta(seconds=GROUP_HEARTBEAT_WINDOW_S + 1)
    store.expire_stale(now=expiry)
    resumed = fixture.tick("--resume", str(PHASE))
    assert resumed.returncode == 0, resumed.stderr
    fresh = store.read()[0]["phases"][str(PHASE)]
    assert fresh["reservation_id"] != old["reservation_id"]
    assert fresh["machine_id"] == "server-01"
    assert fresh["reservation_id"] in claim.read_text()
    stopped = fixture.tick("--stop")
    assert stopped.returncode == 0, stopped.stderr
    store.release_group(fresh)
    available = expiry + dt.timedelta(seconds=GROUP_COOLDOWN_S + 1)
    rival = store.reserve_group([PHASE], "owner-b", now=available)[0]
    # Keep the rival live at the command's real clock; only the expired owner
    # timestamps above use the injected historical clock.
    store.renew_group(rival, status="running")
    refused = fixture.tick("--resume", str(PHASE))
    assert refused.returncode != 0
    assert "board would not grant a fence" in refused.stderr
    assert not (fixture.state / "transport-window").exists()
    assert (fixture.state / "stopped-claim").exists()
    assert store.assert_owner(rival)["machine_id"] == "owner-b"
