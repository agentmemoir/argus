# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The self-test entry point and the checks it collects."""

import shutil
import sys
import tempfile
from pathlib import Path

from argus import conventions
from argus.exec_tempdir import make_exec_dir
from argus.phase_reservations_core import (
    SELF_TEST_BRANCH,
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationFenced,
    _print_json,
)
from argus.phase_reservations_selftest_board import (
    _bad_phase_key_check,
    _deleted_state_check,
)
from argus.phase_reservations_selftest_fixture import (
    _git_test_repo,
    _self_test_check,
    _self_test_receipt,
)
from argus.phase_reservations_selftest_hygiene import (
    _checkout_hygiene_checks,
    drop_checkouts_under,
)
from argus.phase_reservations_selftest_lifecycle import _group_lifecycle_checks
from argus.phase_reservations_selftest_publication import (
    _publication_lock_check,
    _pushurl_boundary_check,
)
from argus.phase_reservations_store import ReservationStore


def self_test_checks():
    """Exercise the shared board with two local clients; no network or repo state."""
    import threading

    conventions.configure(
        {
            "repo-root": Path(),
            "pipeline": None,
            "roadmap-dir": Path("roadmap"),
            "driver.runs": Path("runs"),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": Path(),
            "board.file": Path("claims.json"),
            "board.branch": SELF_TEST_BRANCH,
            "board.remote": "origin",
        }
    )
    checks = []
    # The hygiene fixture writes a `pre-receive` hook and git refuses to run it
    # from a `noexec` mount, so the fixture must live where the host runs a
    # script. A host whose default temporary directory is `noexec` would
    # otherwise report the hook as broken when its mode is correct.
    base = Path(
        make_exec_dir() or tempfile.mkdtemp(prefix="phase-reservations-selftest-")
    )
    try:
        remote, clients = _git_test_repo(base, "claims")
        # Each store owns a different real client checkout. The public side
        # is intentionally absent in this hermetic check; the private repo is
        # the only remote that can receive coordination state.
        root_a = base / "root-a"
        root_b = base / "root-b"
        root_a.mkdir()
        root_b.mkdir()
        store_a = ReservationStore(root_a, private_repo=clients[0])
        store_b = ReservationStore(root_b, private_repo=clients[1])

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim(store, machine):
            barrier.wait()
            try:
                results.append(store.reserve(100601, machine))
            except Exception as exc:  # noqa: BLE001 - test records the failure
                errors.append(exc)

        threads = [
            threading.Thread(target=claim, args=(store_a, "server-01")),
            threading.Thread(target=claim, args=(store_b, "local-01")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        _self_test_check(
            checks,
            "two clients race: one reservation wins",
            len(results) == 1
            and len(errors) == 1
            and isinstance(errors[0], ReservationConflict),
            f"results={len(results)} errors={[type(e).__name__ for e in errors]}",
        )

        state, _commit = store_a.read()
        first = state["phases"].get("100601")
        _self_test_check(
            checks,
            "winning record is complete",
            bool(
                first
                and first.get("reservation_id")
                and first.get("generation") == 1
                and first.get("machine_id") in {"server-01", "local-01"}
            ),
            str(first),
        )

        # The winner can renew/complete; a second phase can be reserved at the
        # same time without changing the first phase's ordering.
        winner_machine = first["machine_id"]
        winner = {
            "phase": "100601",
            "machine_id": winner_machine,
            "reservation_id": first["reservation_id"],
            "generation": first["generation"],
        }
        store_a.renew(winner)
        try:
            store_a.set_status(winner, "completed")
            receipt_required = False
        except CoordinationError:
            receipt_required = True
        _self_test_check(
            checks,
            "completion requires a published receipt",
            receipt_required,
            "missing receipt refused",
        )
        old_runs = conventions.override({"driver.runs": store_a.private / "runs"})
        try:
            receipt = _self_test_receipt(store_a, winner)
            store_a.set_status(winner, "completed", evidence_path=receipt)
            store_a.set_status(winner, "completed", evidence_path=receipt)
        finally:
            conventions.configure(old_runs)
        other = store_b.reserve(100440, "server-01")
        _self_test_check(
            checks,
            "different phases can be reserved independently",
            other["phase"] == "100440",
            str(other),
        )

        # No automatic expiry: an old record remains fenced until the explicit
        # operator takeover increments its generation.
        old = store_a.reserve(100602, "local-01")
        replacement = store_a.takeover(
            100602,
            "server-01",
            confirm=True,
            expected_reservation_id=old["reservation_id"],
            expected_generation=old["generation"],
        )
        try:
            store_a.renew(old)
            fenced = False
        except ReservationFenced:
            fenced = True
        _self_test_check(
            checks,
            "explicit takeover fences old generation",
            fenced and replacement["generation"] == old["generation"] + 1,
            f"old={old} replacement={replacement}",
        )
        try:
            store_a.takeover(
                100602,
                "server-01",
                confirm=True,
                expected_reservation_id=old["reservation_id"],
                expected_generation=old["generation"],
            )
            retry_fenced = False
        except ReservationFenced:
            retry_fenced = True
        _self_test_check(
            checks,
            "takeover retry cannot refence replacement",
            retry_fenced,
            "expected old token",
        )
        rejected = store_a.reserve(100603, "local-01")
        store_a.set_status(rejected, "rejected")
        rejected_takeover = store_a.takeover(
            100603,
            "server-01",
            confirm=True,
            expected_reservation_id=rejected["reservation_id"],
            expected_generation=rejected["generation"],
        )
        _self_test_check(
            checks,
            "rejected work requires explicit takeover",
            rejected_takeover["generation"] == rejected["generation"] + 1,
            f"old={rejected} replacement={rejected_takeover}",
        )

        deleted_ok, deleted_detail = _deleted_state_check(base)
        _self_test_check(
            checks, "deleted state fails closed", deleted_ok, deleted_detail
        )
        key_ok, key_detail = _bad_phase_key_check(base)
        _self_test_check(
            checks, "noncanonical phase keys fail closed", key_ok, key_detail
        )

        # A missing/failed board is unavailable, never treated as an empty
        # board that permits a launch.
        bad = ReservationStore(root_a, private_repo=clients[0], remote="does-not-exist")
        try:
            bad.read()
            unavailable = False
        except CoordinationUnavailable:
            unavailable = True
        _self_test_check(
            checks,
            "coordination fetch failure fails closed",
            unavailable,
            "missing remote",
        )

        lock_contender = store_b.reserve(100604, "server-01")
        pushurl_ok, pushurl_detail = _pushurl_boundary_check(base)
        _self_test_check(
            checks, "public pushurl boundary fails closed", pushurl_ok, pushurl_detail
        )
        _self_test_check(
            checks,
            "publication lock blocks a valid second phase",
            _publication_lock_check(store_a, other, lock_contender),
            "claim-scoped",
        )
        try:
            _group_lifecycle_checks(checks, base)
        except CoordinationError:
            # The shared ``checks`` list already holds whatever the lifecycle
            # proved before it raised, so the named failure survives; this only
            # keeps a fault injected into the lifecycle from aborting the whole
            # self-test before it prints its verdict. A test harness cannot tell
            # a caught guard from a passing one if the run dies first.
            _self_test_check(
                checks,
                "the group lifecycle fixture ran to the end",
                False,
                "the lifecycle raised before it finished",
            )
        _checkout_hygiene_checks(checks, base)
    finally:
        drop_checkouts_under(base)
        shutil.rmtree(base, ignore_errors=True)
    return checks


def self_test():
    checks = self_test_checks()
    ok = all(item["ok"] for item in checks)
    # The named FAIL lines and the verdict go to stderr and the JSON record to
    # stdout, so a harness can look for a named failure without the machine
    # record changing shape for its own callers.
    for item in checks:
        if not item["ok"]:
            print(f"FAIL: {item['check']} ({item['detail']})", file=sys.stderr)
    print(f"coordination self-test: {'pass' if ok else 'FAIL'}", file=sys.stderr)
    _print_json({"coordination_self_test": "pass" if ok else "fail", "checks": checks})
    return 0 if ok else 1
