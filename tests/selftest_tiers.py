#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run the quick tier: the driver self-test cases and guard faults a change affects.

The quick tier is the only gate on a pull request and the local gate for these
two checks. It names every case or fault it runs and the reason, and it stops
at the file list it can place. A changed file that the map cannot place runs
the whole area instead, so an unknown file costs time, never coverage.

    python3 tests/selftest_tiers.py driver [--base REF] [--plan] [--files F...]
    python3 tests/selftest_tiers.py guard  [--base REF] [--plan] [--files F...]

``--plan`` prints the selection and runs nothing. ``--files`` names the changed
files instead of asking git. The full tier (``make self-test``,
``make guard-faults``) is not touched by this module.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_FILE = ROOT / "src/argus/driver/selftest-map.txt"
DRIVER_DIR = ROOT / "src/argus/driver"
# Code under these prefixes can change a self-test result. A path under them that
# the map does not place makes the area run in full.
CODE_ROOTS = ("src/argus/", "bin/")
# A change to any of these changes how the gates run, so every area runs in full.
WHOLE_TREE = (
    "Makefile",
    "pyproject.toml",
    "uv.lock",
    "tests/selftest_tiers.py",
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
CASE_DEF = re.compile(r"^selftest_case_([A-Za-z0-9_]+)\s*\(\)", re.MULTILINE)


@dataclass
class DriverMap:
    """The parsed map file: which driver cases each source file exercises."""

    cases: dict[str, list[str]] = field(default_factory=dict)
    smoke: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """What an area runs: named cases or faults, each with its reason."""

    names: list[str]
    why: dict[str, str]
    full: bool
    notes: list[str] = field(default_factory=list)


def load_map(text: str) -> DriverMap:
    """Parse the map file. A line that is neither a directive nor a pair is an error."""
    driver_map = DriverMap()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "@smoke" and len(parts) == 2:
            driver_map.smoke.append(parts[1])
        elif parts[0] == "@shared" and len(parts) == 2:
            driver_map.shared.append(parts[1])
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
    return [str(item) for item in value]  # type: ignore[union-attr]


def plan_driver(
    changed: list[str], driver_map: DriverMap, all_cases: list[str]
) -> Plan:
    """Choose driver cases for the changed files, or the whole set when unsure."""
    everything = Plan(
        names=list(all_cases),
        why=dict.fromkeys(all_cases, "full driver set"),
        full=True,
    )
    for path in changed:
        if _under(path, WHOLE_TREE):
            everything.notes = [f"whole-tree file changed: {path}"]
            return everything
        if path in driver_map.shared:
            everything.notes = [f"shared file changed: {path}"]
            return everything
    chosen: dict[str, str] = {}
    for name in driver_map.smoke:
        chosen.setdefault(name, "smoke case for the area")
    for path in changed:
        hits = [name for name, paths in driver_map.cases.items() if path in paths]
        for name in hits:
            chosen.setdefault(name, f"maps {path}")
        if not hits and _under(path, CODE_ROOTS):
            everything.notes = [f"unmapped code file changed: {path}"]
            return everything
    names = [name for name in all_cases if name in chosen]
    return Plan(names=names, why=chosen, full=False)


def plan_guard(
    changed: list[str], faults: list[dict[str, object]], driver_map: DriverMap
) -> Plan:
    """Choose guard faults: the ones that patch a changed file or that a changed
    driver file's cases catch. A harness change, or an unmapped code file, runs all."""
    everything = Plan(
        names=[str(f["name"]) for f in faults],
        why={str(f["name"]): "full fault set" for f in faults},
        full=True,
    )
    for path in changed:
        if _under(path, WHOLE_TREE) or _under(path, GUARD_HARNESS):
            everything.notes = [f"fault harness or gate file changed: {path}"]
            return everything
    chosen: dict[str, str] = {}
    if faults:
        chosen.setdefault(str(faults[0]["name"]), "smoke fault for the area")
    for path in changed:
        cases_hit = {name for name, paths in driver_map.cases.items() if path in paths}
        placed = False
        for fault in faults:
            fault_file = str(fault.get("file") or DEFAULT_FAULT_FILE)
            caught_by = set(_names(fault.get("cases")))
            if fault_file == path:
                chosen.setdefault(str(fault["name"]), f"patches {path}")
                placed = True
            elif cases_hit & caught_by:
                chosen.setdefault(str(fault["name"]), f"its case maps {path}")
                placed = True
        if not placed and not cases_hit and _under(path, CODE_ROOTS):
            everything.notes = [f"unmapped code file changed: {path}"]
            return everything
    names = [str(f["name"]) for f in faults if str(f["name"]) in chosen]
    return Plan(names=names, why=chosen, full=False)


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
                f"Fetch the base first (git fetch origin) or pass --base."
            )
        seen.update(line for line in done.stdout.splitlines() if line)
    return sorted(seen)


def _print_plan(area: str, plan: Plan, total: int) -> None:
    what = "cases" if area == "driver" else "faults"
    mode = "the full set" if plan.full else "selected"
    print(f"quick {area}: running {len(plan.names)} of {total} {what} ({mode})")
    for note in plan.notes:
        print(f"  reason for the full set: {note}")
    for name in plan.names:
        print(f"  {name}: {plan.why.get(name, '')}")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("area", choices=("driver", "guard"))
    parser.add_argument(
        "--base", default="origin/main", help="ref the change starts from"
    )
    parser.add_argument(
        "--plan", action="store_true", help="print the plan, run nothing"
    )
    parser.add_argument("--files", nargs="*", default=None, help="changed files")
    args = parser.parse_args(argv)

    changed = args.files if args.files is not None else changed_files(args.base)
    driver_map = load_map(MAP_FILE.read_text(encoding="utf-8"))
    if args.area == "driver":
        all_cases = driver_cases_in_source()
        plan = plan_driver(changed, driver_map, all_cases)
        total = len(all_cases)
    else:
        from faults import FAULTS  # tests/ is this file's directory

        plan = plan_guard(changed, list(FAULTS), driver_map)
        total = len(FAULTS)
    _print_plan(args.area, plan, total)
    if args.plan:
        return 0
    if not plan.names:
        print("quick run: nothing to run for this change")
        return 0
    command, env = _commands(args.area, plan)
    started = time.monotonic()
    rc = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    print(
        f"quick {args.area}: finished in {time.monotonic() - started:.0f} s, exit {rc}"
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
