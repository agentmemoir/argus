#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Request a reservation group for one agent and claim it on the shared board.

An agent identifies itself by its machine id and names one phase. The phase
documents declare the group, so the request resolves the group from the
roadmap, drops the members that are already completed, and claims the
unfinished remainder in one board write under one shared reservation id. The
answer is the array of the members it now owns; there is no separate accept
step, because the request is the claim.

Every refusal fails closed and reserves nothing:

* a group another agent holds names the holding agent;
* a group whose every member is completed fails with ``already completed``;
* a group whose documents carry no declaration names the document;
* a request during the cooldown window reports the time left.

Usage:
    python3 phase_group.py --phase 100420 --machine-id macbook [--config FILE]
    python3 phase_group.py --self-test

Output: on success, a JSON array of the claimed members, each carrying the
shared ``reservation_id``. On refusal, a JSON object with ``state`` and
``error``. Exit 0 on success, 1 for an all-completed group, 2 for a group that
cannot be resolved or a board failure, 3 for a group held elsewhere, and 6 for
a cooldown.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus import conventions
from argus.config import ConfigError
from argus.roadmap_source import resolve_group
from argus.work_source import WorkSourceError
from argus.phase_reservations import (  # noqa: E402 - path set just above
    CoordinationError,
    ReservationConflict,
    ReservationCooldown,
    ReservationStore,
    load_reservation_file,
    reservation_file,
    save_reservation_file,
)

#: Exit code per request state. Anything unlisted is an error, so a new state
#: cannot be mistaken for a successful claim.
REQUEST_EXIT = {
    "RESERVED": 0,
    "ALREADY_COMPLETED": 1,
    "GROUP_UNAVAILABLE": 2,
    "COORDINATION_UNAVAILABLE": 2,
    "WAIT_FOR_CLAIM": 3,
    "COOLDOWN": 6,
}


def _request(repo, roadmap, phase, machine_id, *, store=None):
    """Resolve ``phase`` to its group and claim the unfinished remainder.

    Returns ``(result, exit_code)``. On success ``result`` is the list of
    per-member reservation records, all sharing one reservation id. On any
    refusal it is a JSON-ready dict, and nothing is reserved. The group is
    read from the phase documents through the read-side resolver, so a group
    with no declaration is named rather than guessed.
    """
    try:
        group = resolve_group(repo, roadmap, phase)
    except WorkSourceError as exc:
        result = {
            "state": "GROUP_UNAVAILABLE",
            "phase": str(phase),
            "error": str(exc),
        }
        document = getattr(exc, "document", None)
        if document is not None:
            result["document"] = str(document)
        return result, REQUEST_EXIT["GROUP_UNAVAILABLE"]
    key = group["phase"]
    remainder = list(group["remainder"])
    if not remainder:
        return {
            "state": "ALREADY_COMPLETED",
            "phase": key,
            "group": list(group["group"]),
        }, REQUEST_EXIT["ALREADY_COMPLETED"]
    try:
        store = store or ReservationStore(repo)
        records = store.reserve_group(remainder, machine_id)
        for record in records:
            try:
                save_reservation_file(
                    reservation_file(repo, record["phase"]), record)
            except OSError as exc:
                raise CoordinationError(
                    f"could not persist phase reservation token: {exc}") from exc
    except ReservationCooldown as exc:
        return {
            "state": "COOLDOWN",
            "phase": exc.phase,
            "remaining_seconds": exc.remaining_seconds,
            "error": str(exc),
        }, REQUEST_EXIT["COOLDOWN"]
    except ReservationConflict as exc:
        return {
            "state": "WAIT_FOR_CLAIM",
            "phase": exc.phase,
            "owner": exc.record.get("machine_id"),
            "status": exc.record.get("status"),
            "reservation_id": exc.record.get("reservation_id"),
            "generation": exc.record.get("generation"),
            "error": str(exc),
        }, REQUEST_EXIT["WAIT_FOR_CLAIM"]
    except CoordinationError as exc:
        return {
            "state": "COORDINATION_UNAVAILABLE",
            "phase": key,
            "error": str(exc),
        }, REQUEST_EXIT["COORDINATION_UNAVAILABLE"]
    return records, REQUEST_EXIT["RESERVED"]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default=None,
                        help="one phase in the group to request")
    parser.add_argument("--machine-id", default=None,
                        help="the requesting agent's machine id")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    parser.add_argument("--config")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.phase or not args.machine_id:
        print("phase_group: --phase and --machine-id are required",
              file=sys.stderr)
        return 2
    try:
        conventions.configure_from_args(args)
    except ConfigError as exc:
        print(f"phase_group: {exc}", file=sys.stderr)
        return 2
    repo = Path(conventions.setting("repo-root")).resolve()
    roadmap = conventions.setting("roadmap-dir")
    result, code = _request(repo, roadmap, args.phase, args.machine_id)
    print(json.dumps(result, sort_keys=True))
    return code


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
            code == 2 and result.get("state") == "GROUP_UNAVAILABLE"
            and result.get("document", "").endswith("phase-100010-a.md"),
            result)


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
            result)


def _self_test_group_claim(checks):
    from argus.phase_reservations import SELF_TEST_BRANCH, _git_test_repo
    from argus.exec_tempdir import make_exec_dir

    base = Path(make_exec_dir()
                or tempfile.mkdtemp(prefix="phase-group-selftest-"))
    try:
        root = base / "root"
        root.mkdir()
        # The board checkout is a real git clone, so it is built first and the
        # configured board checkout is pointed at it. A machine names the same
        # checkout in production; here the fixture names the client clone.
        _configure(root)
        _remote, clients = _git_test_repo(base, "phase-group")
        _configure(root, board={
            "board.repo": clients[0],
            "board.file": Path("claims.json"),
            "board.branch": SELF_TEST_BRANCH,
            "board.remote": "origin",
        })
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

        records = store.reserve_group(["100010", "100020", "100030"],
                                      "server-01")
        state, _commit = store.read()
        claimed = [state["phases"].get(k) for k in
                   ("100010", "100020", "100030")]
        shared = {record.get("reservation_id") for record in claimed if record}
        _check(
            checks,
            "a group claim writes every member or none",
            len(records) == 3 and len(claimed) == 3
            and all(claimed) and len(shared) == 1,
            f"records={records} claimed={claimed}")

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
            refused and "100040" not in after["phases"]
            and holder == "server-01",
            f"refused={refused} holder={holder} after={after['phases']}")

        result, code = _request(repo, roadmap, "100050", "server-03")
        array_ok = (code == 0 and isinstance(result, list)
                    and [record["phase"] for record in result]
                    == ["100050", "100060"]
                    and result[0]["reservation_id"] == result[1]["reservation_id"])
        _check(checks,
               "a request answers the group's unfinished remainder as an array",
               array_ok, result)

        tokens_ok = array_ok
        if array_ok:
            for record in result:
                token = load_reservation_file(
                    reservation_file(repo, record["phase"]))
                tokens_ok = tokens_ok and token is not None \
                    and token["reservation_id"] == record["reservation_id"]
        _check(checks,
               "each member's reservation token is written under the shared id",
               tokens_ok, result)

        held, held_code = _request(repo, roadmap, "100060", "server-05",
                                   store=other)
        _check(checks,
               "a group another agent holds names the holding agent",
               held_code == 3 and held.get("state") == "WAIT_FOR_CLAIM"
               and held.get("owner") == "server-03", held)

        # A member already completed is never re-reserved, so the answer is
        # the remainder alone.
        partial, partial_code = _request(repo, roadmap, "100090", "server-07",
                                         store=other)
        _check(checks,
               "a completed member is dropped from the requested remainder",
               partial_code == 0 and isinstance(partial, list)
               and [record["phase"] for record in partial] == ["100090"],
               partial)

        # The cooldown lifecycle lands later; this proves the refusal path a
        # request uses while a claim waits it out.
        class _CooldownStore:
            def reserve_group(self, phases, machine_id, **kwargs):
                raise ReservationCooldown(phases[0], 17, {})

        cooling, cold_code = _request(repo, roadmap, "100070", "server-06",
                                      store=_CooldownStore())
        _check(checks,
               "a request during the cooldown reports the time left",
               cold_code == 6 and cooling.get("state") == "COOLDOWN"
               and cooling.get("remaining_seconds") == 17, cooling)
    finally:
        from argus.phase_reservations import drop_checkouts_under
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


if __name__ == "__main__":
    sys.exit(main())
