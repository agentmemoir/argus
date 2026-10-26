# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-test checks for the claim lifecycle."""

from pathlib import Path
from typing import Any

from argus import conventions
from argus.board.core import (
    COOLDOWN_UNTIL_FIELD,
    EXPIRED_STATUS,
    GROUP_COOLDOWN_S,
    GROUP_HEARTBEAT_WINDOW_S,
    GROUP_START_WINDOW_S,
    ReservationConflict,
    ReservationCooldown,
    ReservationFenced,
)
from argus.board.selftest_fixture import (
    _git_test_repo,
    _self_test_check,
    _self_test_receipt,
)
from argus.board.store import ReservationStore


def _group_lifecycle_checks(checks: list[dict[str, Any]], base: Path) -> None:
    """Prove release, expiry, cooldown and fencing on a two-machine fixture.

    Time is injected through the stores rather than slept through, so the
    start window, the heartbeat window and the cooldown are all exercised in a
    single run. Machine A claims and works; machine B is the second owner.
    """
    import datetime as _dt

    _remote, clients = _git_test_repo(base, "groups")
    root_a = base / "groups-root-a"
    root_b = base / "groups-root-b"
    root_a.mkdir()
    root_b.mkdir()
    store_a = ReservationStore(root_a, private_repo=clients[0])
    store_b = ReservationStore(root_b, private_repo=clients[1])
    epoch = _dt.datetime(2026, 10, 8, 12, 0, tzinfo=_dt.UTC)

    def at(seconds: float) -> _dt.datetime:
        return epoch + _dt.timedelta(seconds=seconds)

    members = ["100200", "100201", "100202"]

    # A group claimed before its start window is live: another machine is
    # refused with a plain conflict, not a cooldown.
    records = store_a.reserve_group(members, "server-01", now=at(0))
    shared = {record["reservation_id"] for record in records}
    _self_test_check(
        checks,
        "a group is claimed under one shared reservation id",
        len(records) == 3 and len(shared) == 1,
        str(records),
    )
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S - 1))
        early: ReservationConflict | ReservationCooldown | None = None
    except ReservationConflict as exc:
        early = exc
    except ReservationCooldown as exc:  # pragma: no cover - clearer failure
        early = exc
    _self_test_check(
        checks,
        "a live group cannot be claimed by a second machine",
        isinstance(early, ReservationConflict),
        f"early={early!r}",
    )

    # Past the start window the whole group expires in one sweep, and the
    # request that found it is refused during the cooldown.
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S + 1))
        cooled = None
    except ReservationCooldown as exc:
        cooled = exc
    state, _commit = store_a.read()
    statuses = [state["phases"].get(key, {}).get("status") for key in members]
    _self_test_check(
        checks,
        "an expired group is refused during the cooldown",
        cooled is not None
        and cooled.remaining_seconds > 0
        and statuses == [EXPIRED_STATUS] * 3,
        f"cooled={cooled!r} statuses={statuses}",
    )
    until = state["phases"][members[0]].get(COOLDOWN_UNTIL_FIELD)
    _self_test_check(
        checks,
        "every member of the expired group shares one cooldown stamp",
        len({state["phases"][key].get(COOLDOWN_UNTIL_FIELD) for key in members}) == 1,
        f"cooldown_until={until!r}",
    )

    # A request never shortens or extends the cooldown.
    try:
        store_b.reserve_group(members, "server-02", now=at(GROUP_START_WINDOW_S + 2))
        cooled_again = None
    except ReservationCooldown as exc:
        cooled_again = exc
    state_again, _commit = store_a.read()
    until_again = state_again["phases"][members[0]].get(COOLDOWN_UNTIL_FIELD)
    _self_test_check(
        checks,
        "a request during cooldown never moves the deadline",
        cooled_again is not None and until_again == until,
        f"{until!r} -> {until_again!r}",
    )

    # A late write from the old owner is fenced after expiry.
    late = {
        "machine_id": "server-01",
        "reservation_id": records[0]["reservation_id"],
        "generation": records[0]["generation"],
    }
    try:
        store_a.renew_group(late, now=at(GROUP_START_WINDOW_S + 2))
        late_fenced = False
    except ReservationFenced:
        late_fenced = True
    _self_test_check(
        checks,
        "a late renew after expiry is fenced",
        late_fenced,
        "expected ReservationFenced",
    )

    # Once the cooldown passes the second machine claims the group.
    later = at(GROUP_START_WINDOW_S + GROUP_COOLDOWN_S + 1)
    after = store_b.reserve_group(members, "server-02", now=later)
    _self_test_check(
        checks,
        "the group is claimable once its cooldown passes",
        [record["phase"] for record in after] == members
        and {record["reservation_id"] for record in after}.isdisjoint(shared),
        str(after),
    )

    # A running group is measured from its heartbeat. renew_group moves the
    # heartbeat for every member at once, which keeps the group alive past the
    # point its original stamp would have expired it.
    running = ["100210", "100211"]
    running_records = store_a.reserve_group(
        running, "server-03", status="running", now=at(0)
    )
    running_token = {
        "machine_id": "server-03",
        "reservation_id": running_records[0]["reservation_id"],
        "generation": running_records[0]["generation"],
    }
    store_a.renew_group(running_token, now=at(GROUP_HEARTBEAT_WINDOW_S - 100))
    refreshed, _commit = store_a.read()
    _self_test_check(
        checks,
        "renew_group refreshes every member's heartbeat",
        len({refreshed["phases"][key]["heartbeat_at"] for key in running}) == 1,
        f"heartbeats={[refreshed['phases'][key]['heartbeat_at'] for key in running]}",
    )
    try:
        store_b.reserve_group(
            running, "server-04", now=at(GROUP_HEARTBEAT_WINDOW_S + 1)
        )
        still_live: ReservationCooldown | ReservationConflict | None = None
    except ReservationCooldown as exc:
        still_live = exc
    except ReservationConflict as exc:
        still_live = exc
    _self_test_check(
        checks,
        "a renewed running group is still live past its first window",
        isinstance(still_live, ReservationConflict),
        f"result={still_live!r}",
    )
    try:
        store_b.reserve_group(
            running, "server-04", now=at(2 * GROUP_HEARTBEAT_WINDOW_S - 99)
        )
        heartbeat_expired = None
    except ReservationCooldown as exc:
        heartbeat_expired = exc
    _self_test_check(
        checks,
        "a running group expires from its heartbeat, not a fixed deadline",
        heartbeat_expired is not None and heartbeat_expired.remaining_seconds > 0,
        f"expired={heartbeat_expired!r}",
    )

    # An explicit release has no cooldown and leaves published work alone.
    published = ["100220", "100221"]
    owned = store_a.reserve_group(published, "server-05", now=at(0))
    token = {
        "machine_id": "server-05",
        "reservation_id": owned[0]["reservation_id"],
        "generation": owned[0]["generation"],
    }
    old_runs = conventions.override({"driver.runs": store_a.private / "runs"})
    try:
        receipt = _self_test_receipt(store_a, owned[1])
        store_a.set_status(owned[1], "completed", evidence_path=receipt)
    finally:
        conventions.configure(old_runs)
    released = store_a.release_group(token, now=at(0))
    after_release, _commit = store_a.read()
    _self_test_check(
        checks,
        "release returns the unfinished member and keeps the published one",
        released == ["100220"]
        and after_release["phases"]["100221"]["status"] == "completed"
        and "100220" not in after_release["phases"],
        f"released={released} phases={sorted(after_release['phases'])}",
    )
    immediate = store_b.reserve_group(["100220"], "server-06", now=at(0))
    _self_test_check(
        checks,
        "an explicit release has no cooldown",
        [record["phase"] for record in immediate] == ["100220"],
        str(immediate),
    )

    # A takeover moves the whole group. One reservation id covers every member,
    # so taking over a single member and leaving the others under the old id
    # would split the group, and release, renewal and expiry all move a group by
    # that shared id.
    takers = ["100230", "100231"]
    taken = store_a.reserve_group(takers, "server-08", now=at(0))
    takeover = store_a.takeover(
        takers[1],
        "server-09",
        confirm=True,
        expected_reservation_id=taken[0]["reservation_id"],
        expected_generation=taken[0]["generation"],
    )
    after_takeover, _commit = store_a.read()
    _self_test_check(
        checks,
        "a takeover moves the whole group under one new reservation id",
        {after_takeover["phases"][key]["reservation_id"] for key in takers}
        == {takeover["reservation_id"]}
        and all(
            after_takeover["phases"][key]["machine_id"] == "server-09" for key in takers
        )
        and takeover["generation"] == taken[0]["generation"] + 1
        and takeover["reservation_id"] != taken[0]["reservation_id"],
        str({key: after_takeover["phases"][key]["reservation_id"] for key in takers}),
    )


__all__ = ["_group_lifecycle_checks"]
