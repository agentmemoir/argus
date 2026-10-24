# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every suite that `make test-all` runs names a real script or module.

The runner starts each suite as a separate process, so a typo in a suite's path
or module name only shows when the suite runs. This test reads the suite list from
the runner's `--list` output and checks each entry, without running any suite.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def suites() -> list[tuple[str, list[str]]]:
    """The suite names and their arguments, as the runner lists them."""
    done = subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, "scripts/test_all.py", "--list"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    listed: list[tuple[str, list[str]]] = []
    for line in done.stdout.splitlines():
        name, _, rest = line.partition(": ")
        listed.append((name, rest.split()))
    return listed


def test_suite_names_are_unique() -> None:
    names = [name for name, _ in suites()]
    assert len(names) == len(set(names)), names


def test_every_suite_names_a_real_script_or_module() -> None:
    for name, args in suites():
        if args[:1] == ["-m"]:
            module = args[1]
            found = importlib.util.find_spec(module) is not None
            assert found, f"{name}: no module named {module}"
        else:
            script = ROOT / args[0]
            assert script.is_file(), f"{name}: no script at {script}"
