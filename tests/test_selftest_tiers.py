# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The quick tier's map and selection: right cases for a change, full set when unsure."""

from __future__ import annotations

import pytest

import selftest_tiers as tiers
from faults import FAULTS

MAP = tiers.load_map(tiers.MAP_FILE.read_text(encoding="utf-8"))
CASES = tiers.driver_cases_in_source()
GROUP_CASES = {"group_run", "group_partial", "group_release", "group_resume"}


def test_the_case_list_is_not_empty():
    assert CASES, "no selftest_case_* definitions were found"


def test_every_case_has_a_map_line():
    missing = sorted(set(CASES) - set(MAP.cases))
    assert not missing, f"cases with no line in selftest-map.txt: {missing}"


def test_every_map_line_names_a_real_case():
    stale = sorted(set(MAP.cases) - set(CASES))
    assert not stale, f"selftest-map.txt names cases that do not exist: {stale}"


def test_smoke_cases_are_real_and_declared():
    assert MAP.smoke, "no @smoke line in selftest-map.txt"
    assert set(MAP.smoke) <= set(CASES)


def test_every_mapped_path_exists():
    paths = {path for paths in MAP.cases.values() for path in paths} | set(MAP.shared)
    gone = sorted(path for path in paths if not (tiers.ROOT / path).is_file())
    assert not gone, f"selftest-map.txt names files that do not exist: {gone}"


def test_a_malformed_map_line_is_refused():
    with pytest.raises(ValueError):
        tiers.load_map("launch")


def test_a_sample_driver_change_runs_only_its_cases():
    plan = tiers.plan_driver(["src/argus/driver/lib-driver-group.sh"], MAP, CASES)
    assert not plan.full
    assert set(plan.names) == GROUP_CASES | {"launch"}


def test_an_unmapped_code_file_runs_the_full_driver_set():
    plan = tiers.plan_driver(["src/argus/config/loader.py"], MAP, CASES)
    assert plan.full
    assert plan.names == CASES
    assert any("src/argus/config/loader.py" in note for note in plan.notes)


def test_a_shared_driver_file_runs_the_full_driver_set():
    plan = tiers.plan_driver(["src/argus/driver/lib-selftest.sh"], MAP, CASES)
    assert plan.full


def test_a_docs_change_runs_only_the_smoke_case():
    plan = tiers.plan_driver(["docs/checks.md"], MAP, CASES)
    assert not plan.full
    assert plan.names == MAP.smoke


def test_a_gate_file_change_runs_the_full_driver_set():
    plan = tiers.plan_driver(["Makefile"], MAP, CASES)
    assert plan.full


def test_a_guard_fault_follows_the_file_it_patches():
    plan = tiers.plan_guard(["src/argus/driver/lib-driver-run.sh"], FAULTS, MAP)
    assert not plan.full
    assert "release-writes-halt-marker" in plan.names


def test_an_unmapped_code_file_runs_the_full_fault_set():
    plan = tiers.plan_guard(["src/argus/zz_unplaced.py"], FAULTS, MAP)
    assert plan.full
    assert len(plan.names) == len(FAULTS)


def test_a_fault_harness_change_runs_the_full_fault_set():
    plan = tiers.plan_guard(["tests/guard_faults_engine.py"], FAULTS, MAP)
    assert plan.full


def test_every_fault_names_a_file_that_exists():
    gone = [
        f["name"]
        for f in FAULTS
        if not (tiers.ROOT / str(f.get("file") or tiers.DEFAULT_FAULT_FILE)).is_file()
    ]
    assert not gone, f"faults whose file does not exist: {gone}"
