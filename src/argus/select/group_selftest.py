#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The hermetic self-test for the reservation-group request program.

Split out of ``phase_group.py`` so that file stays the program: the request, the
release and the renew, and nothing else. ``phase_group.py --self-test`` still runs
exactly these checks and prints the same verdict line, because the Makefile and
the guard-fault harness both read it.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

if not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _bootstrap  # noqa: F401 - register the source package
from argus import conventions
from argus.board.reservations import (
    ReservationConflict,
    ReservationCooldown,
    ReservationStore,
    load_reservation_file,
    reservation_file,
)
from argus.select.group import _release, _renew, _request


def _check(checks, name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _configure(repo, *, board=None):
    settings = {
        "repo-root": repo,
        "pipeline": None,
        "roadmap-dir": repo / "roadmap",
        "driver.include": ["phase-*.md"],
        "driver.exclude": [],
        "driver.runs": repo / "runs",
        "machine.id": "self-test",
        "commands.git": "git",
        "commands.gh": "gh",
    }
    if board is not None:
        settings.update(board)
    conventions.configure(settings)


def _write_group(roadmap, members):
    """Write one symmetric together declaration into every member file."""
    text = "### Reservation Group\n\n`together: " + ", ".join(members) + "`\n"
    for member in members:
        (roadmap / f"phase-{member}-x.md").write_text(text)


def _self_test_missing_declaration(checks):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        roadmap = repo / "roadmap"
        roadmap.mkdir()
        (roadmap / "phase-100010-a.md").write_text("## 1. Objective\n")
        _configure(repo)
        result, code = _request(repo, roadmap, "100010", "server-01")
        _check(
            checks,
            "a phase with no declaration is refused and names the document",
            code == 2
            and result.get("state") == "GROUP_UNAVAILABLE"
            and result.get("document", "").endswith("phase-100010-a.md"),
            result,
        )


def _self_test_already_completed(checks):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp).resolve()
        roadmap = repo / "roadmap"
        roadmap.mkdir()
        _write_group(roadmap, ["100010", "100020"])
        for member in ("100010", "100020"):
            run = repo / "runs" / f"phase-{member}"
            run.mkdir(parents=True)
            (run / "run.json").write_text(json.dumps({"state": "completed"}))
        _configure(repo)
        result, code = _request(repo, roadmap, "100010", "server-01")
        _check(
            checks,
            "an all-completed group is refused as already completed",
            code == 1 and result.get("state") == "ALREADY_COMPLETED",
            result,
        )


def _self_test_group_claim(checks):
    from argus.board.reservations import SELF_TEST_BRANCH, _git_test_repo
    from argus.exec_tempdir import make_exec_dir

    base = Path(make_exec_dir() or tempfile.mkdtemp(prefix="phase-group-selftest-"))
    try:
        root = base / "root"
        root.mkdir()
        # The board checkout is a real git clone, so it is built first and the
        # configured board checkout is pointed at it. A machine names the same
        # checkout in production; here the fixture names the client clone.
        _configure(root)
        _remote, clients = _git_test_repo(base, "phase-group")
        _configure(
            root,
            board={
                "board.repo": clients[0],
                "board.file": Path("claims.json"),
                "board.branch": SELF_TEST_BRANCH,
                "board.remote": "origin",
            },
        )
        repo = root
        roadmap = root / "roadmap"
        roadmap.mkdir()
        _write_group(roadmap, ["100010", "100020", "100030"])
        _write_group(roadmap, ["100050", "100060"])
        _write_group(roadmap, ["100070", "100080"])
        _write_group(roadmap, ["100090", "100100"])
        done_run = root / "runs" / "phase-100100"
        done_run.mkdir(parents=True)
        (done_run / "run.json").write_text(json.dumps({"state": "completed"}))
        store = ReservationStore(repo, private_repo=clients[0])
        other = ReservationStore(repo, private_repo=clients[1])

        records = store.reserve_group(["100010", "100020", "100030"], "server-01")
        state, _commit = store.read()
        claimed = [state["phases"].get(k) for k in ("100010", "100020", "100030")]
        shared = {record.get("reservation_id") for record in claimed if record}
        _check(
            checks,
            "a group claim writes every member or none",
            len(records) == 3
            and len(claimed) == 3
            and all(claimed)
            and len(shared) == 1,
            f"records={records} claimed={claimed}",
        )

        try:
            other.reserve_group(["100040", "100020", "100030"], "server-02")
            refused = False
            holder = None
        except ReservationConflict as exc:
            refused = True
            holder = exc.record.get("machine_id")
        after, _commit = store.read()
        _check(
            checks,
            "a conflict on one member refuses the whole claim",
            refused and "100040" not in after["phases"] and holder == "server-01",
            f"refused={refused} holder={holder} after={after['phases']}",
        )

        result, code = _request(repo, roadmap, "100050", "server-03")
        array_ok = (
            code == 0
            and isinstance(result, list)
            and [record["phase"] for record in result] == ["100050", "100060"]
            and result[0]["reservation_id"] == result[1]["reservation_id"]
        )
        _check(
            checks,
            "a request answers the group's unfinished remainder as an array",
            array_ok,
            result,
        )

        tokens_ok = array_ok
        if array_ok:
            for record in result:
                token = load_reservation_file(reservation_file(repo, record["phase"]))
                tokens_ok = (
                    tokens_ok
                    and token is not None
                    and token["reservation_id"] == record["reservation_id"]
                )
        _check(
            checks,
            "each member's reservation token is written under the shared id",
            tokens_ok,
            result,
        )

        held, held_code = _request(repo, roadmap, "100060", "server-05", store=other)
        _check(
            checks,
            "a group another agent holds names the holding agent",
            held_code == 3
            and held.get("state") == "WAIT_FOR_CLAIM"
            and held.get("owner") == "server-03",
            held,
        )

        # A member already completed is never re-reserved, so the answer is
        # the remainder alone.
        partial, partial_code = _request(
            repo, roadmap, "100090", "server-07", store=other
        )
        _check(
            checks,
            "a completed member is dropped from the requested remainder",
            partial_code == 0
            and isinstance(partial, list)
            and [record["phase"] for record in partial] == ["100090"],
            partial,
        )

        # The cooldown lifecycle lands later; this proves the refusal path a
        # request uses while a claim waits it out.
        class _CooldownStore:
            def read(self):
                return {"phases": {}}, None

            def reserve_group(self, phases, machine_id, **kwargs):  # noqa: ARG002 - the fake mirrors the real store's signature
                raise ReservationCooldown(phases[0], 17, {})

        cooling, cold_code = _request(
            repo, roadmap, "100070", "server-06", store=_CooldownStore()
        )
        _check(
            checks,
            "a request during the cooldown reports the time left",
            cold_code == 6
            and cooling.get("state") == "COOLDOWN"
            and cooling.get("remaining_seconds") == 17,
            cooling,
        )

        # A second request from the owner answers the same array: the call is
        # idempotent, and an owner is never refused its own reservation.
        again, again_code = _request(repo, roadmap, "100050", "server-03", store=store)
        _check(
            checks,
            "a repeated request from the holder answers the same array",
            again_code == 0
            and isinstance(again, list)
            and [record["phase"] for record in again]
            == [record["phase"] for record in result]
            and again[0]["reservation_id"] == result[0]["reservation_id"],
            again,
        )

        class _Args:
            machine_id = None
            reservation_id = None
            generation = None
            status = None

        renewed, renewed_code = _renew(repo, "100050", _Args(), store=store)
        _check(
            checks,
            "a renew refreshes the whole group from the phase's own token",
            renewed_code == 0
            and renewed.get("state") == "RENEWED"
            and renewed.get("members") == ["100050", "100060"],
            renewed,
        )

        released, released_code = _release(repo, "100050", _Args(), store=store)
        after_release, _commit = store.read()
        _check(
            checks,
            "a release gives the whole group back from the token alone",
            released_code == 0
            and released.get("state") == "RELEASED"
            and sorted(released.get("released") or []) == ["100050", "100060"]
            and "100050" not in after_release["phases"],
            released,
        )
    finally:
        from argus.board.reservations import drop_checkouts_under

        drop_checkouts_under(base)
        shutil.rmtree(base, ignore_errors=True)


def self_test():
    """Exercise the request rules and the atomic group claim; needs no network."""
    checks = []
    _self_test_missing_declaration(checks)
    _self_test_already_completed(checks)
    _self_test_group_claim(checks)
    failed = [check for check in checks if not check["ok"]]
    for check in failed:
        print(f"FAIL: {check['check']} ({check['detail']})")
    if failed:
        print("phase_group self-test: FAIL")
        return 1
    print(f"phase_group self-test: pass ({len(checks)} checks)")
    return 0
