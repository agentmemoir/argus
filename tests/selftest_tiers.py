#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run the quick tier: the driver self-test cases and guard faults a change affects.

The quick tier is the only gate on a pull request and the local gate for these
two checks. It names every case or fault it runs and the reason, and it stops
at the file list it can place. A changed file that nothing places runs the whole
area instead, so an unknown file costs time, never coverage.

    python3 tests/selftest_tiers.py driver [--base REF] [--plan] [--files F...]
    python3 tests/selftest_tiers.py guard  [--base REF] [--plan] [--files F...]
    python3 tests/selftest_tiers.py driver --matrix      # shard list for CI
    python3 tests/selftest_tiers.py driver --cases "a b" --shard 2

``--plan`` prints the selection and runs nothing. ``--files`` names the changed
files instead of asking git. ``--matrix`` prints the shards as JSON, for CI, and
puts the plan on stderr. ``--cases`` runs exactly the named cases or faults. The
full tier (``make self-test``, ``make guard-faults``) is not touched here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from selftest_closure import case_reach

ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = ROOT / "src/argus/driver/selftest-map.txt"
DRIVER_DIR = ROOT / "src/argus/driver"
# Code under these prefixes can change a self-test result. A path under them that
# nothing places makes the area run in full.
CODE_ROOTS = ("src/argus/", "bin/")
# A change to any of these changes how the gates run, so every area runs in full.
WHOLE_TREE = (
    "Makefile",
    "pyproject.toml",
    "uv.lock",
    "tests/selftest_tiers.py",
    "tests/selftest_closure.py",
    ".github/workflows/",
)
# The fault harness itself. A change to it runs every fault.
GUARD_HARNESS = (
    "tests/faults",
    "tests/guard_faults",
    "tests/issue_faults",
    "tests/issue_guards",
)
DEFAULT_FAULT_FILE = "src/argus/driver/phase-driver.sh"
# Shards per CI gate. Each shard runs on its own runner, so the gate's wall time is
# its longest shard, not the sum.
SHARDS = {"driver": 6, "guard": 8}
CASE_DEF = re.compile(r"^selftest_case_([A-Za-z0-9_]+)\s*\(\)", re.MULTILINE)


@dataclass
class DriverMap:
    """The parsed map file: the files each case names, and the smoke and shared lists."""

    cases: dict[str, list[str]]
    smoke: list[str]
    shared: list[str]
    unplaced: list[str]


@dataclass
class Plan:
    """What an area runs: named cases or faults, each with its reason."""

    names: list[str]
    why: dict[str, str]
    full: bool
    notes: list[str]


def load_map(text: str) -> DriverMap:
    """Parse the map file. A line that is neither a directive nor a pair is an error."""
    driver_map = DriverMap(cases={}, smoke=[], shared=[], unplaced=[])
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "@smoke" and len(parts) == 2:
            driver_map.smoke.append(parts[1])
        elif parts[0] == "@shared" and len(parts) == 2:
            driver_map.shared.append(parts[1])
        elif parts[0] == "@unplaced" and len(parts) == 2:
            driver_map.unplaced.append(parts[1])
        elif len(parts) == 2 and not parts[0].startswith("@"):
            driver_map.cases.setdefault(parts[0], []).append(parts[1])
        else:
            raise ValueError(f"selftest-map.txt line {number}: not a pair: {raw!r}")
    return driver_map


def driver_cases_in_source(driver_dir: Path = DRIVER_DIR) -> list[str]:
    """Every case the self-test defines, in file order, from its lib-selftest files."""
    found: list[str] = []
    for path in sorted(driver_dir.glob("lib-selftest*.sh")):
        found.extend(CASE_DEF.findall(path.read_text(encoding="utf-8")))
    return found


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p) for p in prefixes)


def _names(value: object) -> list[str]:
    """A fault's `cases` value is one name or a list; both become a list."""
    if not value:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, (list, tuple)):
        items = cast("Sequence[object]", value)
        return [str(item) for item in items]
    return []


def placements(
    path: str, driver_map: DriverMap, reach: Mapping[str, set[str]]
) -> set[str]:
    """The cases that run for a changed file: those whose map lines name it, and
    those whose Python entries reach it through imports."""
    named = {case for case, paths in driver_map.cases.items() if path in paths}
    reached = {case for case, files in reach.items() if path in files}
    return named | reached


def plan_driver(
    changed: list[str],
    driver_map: DriverMap,
    all_cases: list[str],
    reach: Mapping[str, set[str]],
) -> Plan:
    """Choose driver cases for the changed files, or the whole set when unsure."""
    for path in changed:
        if _under(path, WHOLE_TREE) or path in driver_map.shared:
            return _everything(
                all_cases, "full driver set", [f"gate or shared file: {path}"]
            )
    chosen: dict[str, str] = {}
    for name in driver_map.smoke:
        chosen.setdefault(name, "smoke case for the area")
    for path in changed:
        hits = placements(path, driver_map, reach)
        for name in sorted(hits):
            chosen.setdefault(name, f"reaches {path}")
        if not hits and _under(path, CODE_ROOTS):
            return _everything(
                all_cases, "full driver set", [f"unplaced code file: {path}"]
            )
    names = [name for name in all_cases if name in chosen]
    return Plan(names=names, why=chosen, full=False, notes=[])


def _everything(names: list[str], reason: str, notes: list[str]) -> Plan:
    return Plan(
        names=list(names),
        why=dict.fromkeys(names, reason),
        full=True,
        notes=notes,
    )


def plan_guard(
    changed: list[str],
    faults: Sequence[Mapping[str, object]],
    driver_map: DriverMap,
    reach: Mapping[str, set[str]],
) -> Plan:
    """Choose guard faults: the ones that patch a changed file, or whose catching cases
    a changed file reaches. A harness change or an unplaced code file runs all."""
    all_names = [str(f["name"]) for f in faults]
    for path in changed:
        if _under(path, WHOLE_TREE) or _under(path, GUARD_HARNESS):
            return _everything(
                all_names, "full fault set", [f"gate or harness file: {path}"]
            )
    chosen: dict[str, str] = {}
    if faults:
        chosen.setdefault(str(faults[0]["name"]), "smoke fault for the area")
    for path in changed:
        cases_hit = placements(path, driver_map, reach)
        placed = False
        for fault in faults:
            fault_file = str(fault.get("file") or DEFAULT_FAULT_FILE)
            if fault_file == path:
                chosen.setdefault(str(fault["name"]), f"patches {path}")
                placed = True
            elif cases_hit & set(_names(fault.get("cases"))):
                chosen.setdefault(str(fault["name"]), f"its case reaches {path}")
                placed = True
        if not placed and not cases_hit and _under(path, CODE_ROOTS):
            return _everything(
                all_names, "full fault set", [f"unplaced code file: {path}"]
            )
    names = [name for name in all_names if name in chosen]
    return Plan(names=names, why=chosen, full=False, notes=[])


def shard_names(names: list[str], count: int) -> list[list[str]]:
    """Split names into at most ``count`` shards, dealt round-robin in declared order."""
    size = max(1, min(count, len(names)))
    return [names[i::size] for i in range(size)]


def changed_files(base: str, root: Path = ROOT) -> list[str]:
    """Files changed since the base, in the branch, the work tree, and untracked."""
    commands = (
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    seen: set[str] = set()
    for command in commands:
        done = subprocess.run(
            command, cwd=root, capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            raise SystemExit(
                f"quick tier: {' '.join(command)} failed: {done.stderr.strip()}\n"
                "Fetch the base first (git fetch origin) or pass --base."
            )
        seen.update(line for line in done.stdout.splitlines() if line)
    return sorted(seen)


def _print_plan(area: str, plan: Plan, total: int, out: TextIO) -> None:
    what = "cases" if area == "driver" else "faults"
    mode = "the full set" if plan.full else "selected"
    print(
        f"quick {area}: running {len(plan.names)} of {total} {what} ({mode})", file=out
    )
    for note in plan.notes:
        print(f"  reason for the full set: {note}", file=out)
    for name in plan.names:
        print(f"  {name}: {plan.why.get(name, '')}", file=out)


def _commands(area: str, plan: Plan) -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    if area == "driver":
        env.pop("SELFTEST_CASES", None)
        if not plan.full:
            env["SELFTEST_CASES"] = " ".join(plan.names)
        return [str(ROOT / "bin/argus"), "--self-test"], env
    command = [sys.executable, str(ROOT / "tests/guard_faults.py")]
    if not plan.full:
        for name in plan.names:
            command.extend(["--only", name])
    return command, env


def _load(area: str) -> tuple[list[str], Sequence[Mapping[str, object]]]:
    """The case names of the driver area, or the fault table of the guard area."""
    if area == "driver":
        return driver_cases_in_source(), []
    from faults import FAULTS  # tests/ is this file's directory

    return [], list(FAULTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("area", choices=("driver", "guard"))
    parser.add_argument(
        "--base", default="origin/main", help="ref the change starts from"
    )
    parser.add_argument(
        "--plan", action="store_true", help="print the plan, run nothing"
    )
    parser.add_argument("--files", nargs="*", default=None, help="changed files")
    parser.add_argument("--matrix", action="store_true", help="print CI shards as JSON")
    parser.add_argument("--cases", default=None, help="run exactly these names")
    parser.add_argument("--shard", default="", help="label for a CI shard")
    args = parser.parse_args(argv)

    driver_map = load_map(MAP_FILE.read_text(encoding="utf-8"))
    cases, faults = _load(args.area)
    reach = case_reach(ROOT, driver_map.cases)
    total = len(cases) if args.area == "driver" else len(faults)
    if args.cases is not None:
        names = args.cases.split()
        plan = Plan(
            names=names,
            why=dict.fromkeys(names, "named by the CI shard"),
            full=False,
            notes=[],
        )
    else:
        changed = args.files if args.files is not None else changed_files(args.base)
        if args.area == "driver":
            plan = plan_driver(changed, driver_map, cases, reach)
        else:
            plan = plan_guard(changed, faults, driver_map, reach)
    if args.matrix:
        shards = shard_names(plan.names, SHARDS[args.area])
        _print_plan(args.area, plan, total, sys.stderr)
        print(
            json.dumps(
                [{"id": i + 1, "names": " ".join(s)} for i, s in enumerate(shards)]
            )
        )
        return 0
    _print_plan(args.area, plan, total, sys.stdout)
    if args.plan:
        return 0
    if not plan.names:
        print("quick run: nothing to run for this change")
        return 0
    command, env = _commands(args.area, plan)
    label = f"shard {args.shard}" if args.shard else "run"
    started = time.monotonic()
    rc = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    print(
        f"quick {args.area} {label}: finished in {time.monotonic() - started:.0f} s, exit {rc}"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
