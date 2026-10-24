# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The quick tier's map and selection: right cases for a change, full set when unsure."""

from __future__ import annotations

import pytest

import selftest_shards
import selftest_tiers as tiers
from faults import FAULTS
from selftest_closure import case_reach

MAP = tiers.load_map(tiers.MAP_FILE.read_text(encoding="utf-8"))
CASES = tiers.driver_cases_in_source()
REACH = case_reach(tiers.ROOT, MAP.cases)
GROUP_CASES = {"group_run", "group_partial", "group_release", "group_resume"}
SYNTHETIC_GROUP = ("launch", "flow", "release")


def _code_files() -> list[str]:
    """Every file a driver self-test case can run: argus source, driver shell, the entry."""
    found = [
        p.relative_to(tiers.ROOT).as_posix()
        for p in (tiers.ROOT / "src/argus").rglob("*.py")
    ]
    found += [
        p.relative_to(tiers.ROOT).as_posix() for p in tiers.DRIVER_DIR.glob("*.sh")
    ]
    found.append("bin/argus")
    return sorted(found)


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
    named = {path for paths in MAP.cases.values() for path in paths}
    named |= set(MAP.shared) | set(MAP.unplaced)
    gone = sorted(path for path in named if not (tiers.ROOT / path).is_file())
    assert not gone, f"selftest-map.txt names files that do not exist: {gone}"


def test_every_code_file_is_placed_or_declared_unplaced():
    """The fallback set is exactly the declared list. A new file that nothing places
    fails here, so it is placed in the map or declared, never left to the fallback by
    accident."""
    unplaced = sorted(
        path
        for path in _code_files()
        if path not in MAP.shared and not tiers.placements(path, MAP, REACH)
    )
    assert unplaced == sorted(MAP.unplaced), (
        f"unplaced now: {unplaced}\ndeclared: {sorted(MAP.unplaced)}"
    )


def test_a_malformed_map_line_is_refused():
    with pytest.raises(ValueError):
        tiers.load_map("launch")


def test_a_sample_driver_change_runs_only_its_cases():
    plan = tiers.plan_driver(
        ["src/argus/driver/lib-driver-group.sh"], MAP, CASES, REACH
    )
    assert not plan.full
    assert set(plan.names) == GROUP_CASES | {"launch"}


def test_a_python_module_only_the_import_closure_reaches_is_placed():
    plan = tiers.plan_driver(["src/argus/board/records.py"], MAP, CASES, REACH)
    assert not plan.full
    assert plan.names


def test_a_declared_unplaced_file_runs_the_full_driver_set():
    plan = tiers.plan_driver(["src/argus/estimate.py"], MAP, CASES, REACH)
    assert plan.full
    assert plan.names == CASES
    assert any("src/argus/estimate.py" in note for note in plan.notes)


def test_a_shared_driver_file_runs_the_full_driver_set():
    plan = tiers.plan_driver(["src/argus/driver/lib-selftest.sh"], MAP, CASES, REACH)
    assert plan.full


def test_a_docs_change_runs_only_the_smoke_case():
    plan = tiers.plan_driver(["docs/checks.md"], MAP, CASES, REACH)
    assert not plan.full
    assert plan.names == MAP.smoke


def test_a_gate_file_change_runs_the_full_driver_set():
    plan = tiers.plan_driver(["Makefile"], MAP, CASES, REACH)
    assert plan.full


def test_a_guard_fault_follows_the_file_it_patches():
    plan = tiers.plan_guard(["src/argus/driver/lib-driver-run.sh"], FAULTS, MAP, REACH)
    assert not plan.full
    assert "release-writes-halt-marker" in plan.names


def test_a_declared_unplaced_file_runs_the_full_fault_set():
    plan = tiers.plan_guard(["src/argus/estimate.py"], FAULTS, MAP, REACH)
    assert plan.full
    assert len(plan.names) == len(FAULTS)


def test_a_fault_harness_change_runs_the_full_fault_set():
    plan = tiers.plan_guard(["tests/guard_faults_engine.py"], FAULTS, MAP, REACH)
    assert plan.full


def test_the_shards_cover_every_driver_case_once():
    units = selftest_shards.driver_units(CASES)
    shards = selftest_shards.shard_units(units, 6)
    assert len(shards) == 6
    assert sorted(name for shard in shards for name in shard) == sorted(CASES)


def test_an_ordered_group_stays_in_one_shard():
    units = selftest_shards.driver_units(CASES, groups=[SYNTHETIC_GROUP])
    shards = selftest_shards.shard_units(units, 6)
    groups = [unit for unit in units if len(unit) > 1]
    assert groups, "no ordered group was formed from the synthetic group"
    for group in groups:
        homes = {i for i, shard in enumerate(shards) for name in group if name in shard}
        assert len(homes) == 1, f"ordered group split across shards: {group}"


def test_a_member_of_an_ordered_group_brings_the_whole_group():
    units = selftest_shards.driver_units(["flow"], groups=[("launch", "flow")])
    assert len(units) == 1
    assert "launch" in units[0] and "flow" in units[0]


def test_every_fault_names_a_file_that_exists():
    gone = [
        f["name"]
        for f in FAULTS
        if not (tiers.ROOT / str(f.get("file") or tiers.DEFAULT_FAULT_FILE)).is_file()
    ]
    assert not gone, f"faults whose file does not exist: {gone}"
