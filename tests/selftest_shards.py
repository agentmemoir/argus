# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Deal the quick tier's driver cases into CI shards, keeping ordered groups whole.

Some driver cases need the state the cases before them leave, so scripts/driver_cases.py
runs each ordered group in one process, in declared order. A shard must keep each group
whole, or a case would run without the state it needs. This module takes the units from
that script, so the grouping has one source of truth.

The ordered group that runs as one shard is tracked in #270, until its cases are
independent and the group can be split.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
DRIVER_CASES = ROOT / "scripts" / "driver_cases.py"
_Jobs = Callable[[list[str]], list[tuple[str, ...]]]


def driver_units(
    names: list[str], groups: list[tuple[str, ...]] | None = None
) -> list[list[str]]:
    """The units a driver run takes, as scripts/driver_cases.py forms them: one per case
    outside an ordered group, and one per ordered group in declared order. ``groups``
    replaces the script's own groups, so a test can check the grouping with a group."""
    spec = importlib.util.spec_from_file_location("driver_cases", DRIVER_CASES)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {DRIVER_CASES}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if groups is not None:
        module.__dict__["ORDERED_GROUPS"] = groups
    jobs = cast(_Jobs, module.jobs)
    return [list(unit) for unit in jobs(names)]


def guard_units(names: list[str]) -> list[list[str]]:
    """Guard faults are independent, so each one is a unit of its own."""
    return [[name] for name in names]


def shard_units(units: list[list[str]], count: int) -> list[list[str]]:
    """Deal whole units into at most ``count`` shards, round-robin in order."""
    size = max(1, min(count, len(units)))
    shards: list[list[str]] = [[] for _ in range(size)]
    for index, unit in enumerate(units):
        shards[index % size].extend(unit)
    return shards
