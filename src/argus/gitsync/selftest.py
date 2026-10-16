# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The self-test entry point and the delegated checks it collects."""

import json
import shutil
from pathlib import Path
from typing import Any

from argus import conventions
from argus.exec_tempdir import make_exec_dir
from argus.gitsync.modes import result_exit_code
from argus.gitsync.selftest_branch import (
    _phase_branch_checks,
    _phase_branch_name_checks,
)
from argus.gitsync.selftest_carry import (
    _group_branch_collision_checks,
    _handoff_checks,
    _receipt_carry_checks,
)
from argus.gitsync.selftest_dispatch import _tidy_and_dispatch_checks
from argus.gitsync.selftest_fixture import BOARD_REL, _check
from argus.gitsync.selftest_group_checklist import _group_checklist_checks
from argus.gitsync.selftest_group_gate import _group_gate_checks
from argus.gitsync.selftest_group_review import _group_review_checks
from argus.gitsync.selftest_group_sequence import _group_sequence_checks
from argus.gitsync.selftest_merge_gate import _branch_pair_checks, _merge_gate_checks
from argus.gitsync.selftest_publish import (
    _publish_still_refuses_check,
    _publish_while_writing_check,
)
from argus.gitsync.selftest_receipt import (
    _group_publication_check,
    _portable_publication_check,
    _publication_receipt_check,
)
from argus.gitsync.selftest_repair import _repair_step_checks
from argus.gitsync.selftest_runstate import _runstate_checks
from argus.gitsync.selftest_sequencing import _sequencing_checks
from argus.gitsync.selftest_stack import _two_phase_stack_checks
from argus.gitsync.selftest_stale import _stale_receipt_checks, _tree_proof_checks
from argus.gitsync.selftest_sync import _sync_one_checks
from argus.gitsync.selftest_sync_refuse import _sync_refuse_checks


def self_test_checks() -> list[dict[str, Any]]:
    """Run checks with explicit temporary layout settings."""
    conventions.configure(
        {
            "repo-root": Path(),
            "pipeline": None,
            "roadmap-dir": BOARD_REL / "roadmap",
            "driver.runs": BOARD_REL / "runs",
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": BOARD_REL,
            "board.file": Path("claims.json"),
            "board.branch": "main",
            "board.remote": "origin",
        }
    )
    return _self_test_checks()


def _self_test_checks() -> list[dict[str, Any]]:
    """Stubbed-remote checks. Pure: temp dirs only, no repo state touched."""
    checks: list[dict[str, Any]] = []
    # The stubbed remotes carry hooks and fixtures git runs, so the base must be
    # storage the host executes; when none permits it, record that rather than
    # fall back to a directory that refuses.
    executable = make_exec_dir()
    if not executable:
        _check(
            checks,
            "sync self-test needs temporary storage the host will execute",
            False,
            "no temporary directory on this host permits execution",
        )
        return checks
    base = Path(executable)
    try:
        _sync_one_checks(base, checks)
        _sync_refuse_checks(base, checks)
        _runstate_checks(base, checks)
        _delegated_checks(base, checks)
    finally:
        # The fixture is scratch, and this run is the only thing that knows where
        # it is: left behind, it is one more directory for the driver's recovery
        # sweep to find, which is how 70-odd of them accumulated in one temporary
        # directory.
        shutil.rmtree(base, ignore_errors=True)

    return checks


def _delegated_checks(base: Path, checks: list[dict[str, Any]]) -> None:

    _check(
        checks,
        "publish: coordination failure maps to exit 2",
        result_exit_code({"ok": False, "state": "COORDINATION_UNAVAILABLE"}) == 2
        and result_exit_code({"ok": False, "state": "FENCED"}) == 1
        and result_exit_code({"ok": True, "state": "PUBLISHED"}) == 0
        and result_exit_code({"ok": True, "state": "AWAITING_REVIEW"}) == 0,
        "state-specific exit codes",
    )
    receipt_ok, receipt_detail = _publication_receipt_check(base)
    _check(checks, "publish: receipt-backed completion", receipt_ok, receipt_detail)

    group_ok, group_detail = _group_publication_check(base)
    _check(
        checks,
        "group: one branch, one pull request, one receipt per member",
        group_ok,
        group_detail,
    )

    race_ok, race_detail = _publish_while_writing_check(base)
    _check(
        checks,
        "publish: succeeds first try with the engine writing the marker",
        race_ok,
        race_detail,
    )
    for name, ok, detail in _publish_still_refuses_check(base):
        _check(checks, f"publish: {name}", ok, detail)
    for name, ok, detail in _phase_branch_name_checks(base):
        _check(checks, f"branch name: {name}", ok, detail)
    for name, ok, detail in _phase_branch_checks(base):
        _check(checks, f"branch: {name}", ok, detail)
    for name, ok, detail in _branch_pair_checks(base):
        _check(checks, f"branch pair: {name}", ok, detail)
    for name, ok, detail in _merge_gate_checks(base):
        _check(checks, f"merge gate: {name}", ok, detail)
    for name, ok, detail in _receipt_carry_checks(base):
        _check(checks, f"receipt carry: {name}", ok, detail)
    for name, ok, detail in _sequencing_checks(base):
        _check(checks, f"sequencing: {name}", ok, detail)
    for name, ok, detail in _group_review_checks(base):
        _check(checks, f"group pull request: {name}", ok, detail)
    for name, ok, detail in _group_checklist_checks(base):
        _check(checks, f"group checklist: {name}", ok, detail)
    for name, ok, detail in _group_gate_checks(base):
        _check(checks, f"group gate: {name}", ok, detail)
    for name, ok, detail in _repair_step_checks(base):
        _check(checks, f"repair: {name}", ok, detail)
    for name, ok, detail in _group_sequence_checks(base):
        _check(checks, f"group sequence: {name}", ok, detail)
    for name, ok, detail in _tidy_and_dispatch_checks(base):
        _check(checks, f"dispatch: {name}", ok, detail)
    for name, ok, detail in _two_phase_stack_checks(base):
        _check(checks, f"stack: {name}", ok, detail)
    for name, ok, detail in _stale_receipt_checks(base):
        _check(checks, f"stale receipt: {name}", ok, detail)
    for name, ok, detail in _tree_proof_checks(base):
        _check(checks, f"tree proof: {name}", ok, detail)

    portable_ok, portable_detail = _portable_publication_check(base)
    _check(checks, "publish: custom project conventions", portable_ok, portable_detail)

    for name, ok, detail in _handoff_checks(base):
        _check(checks, f"handoff: {name}", ok, detail)
    for name, ok, detail in _group_branch_collision_checks(base):
        _check(checks, f"group name: {name}", ok, detail)


def self_test() -> int:
    checks = self_test_checks()
    ok = all(c["ok"] for c in checks)
    print(
        json.dumps(
            {"sync_self_test": "pass" if ok else "fail", "checks": checks}, indent=2
        )
    )
    return 0 if ok else 1
