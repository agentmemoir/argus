# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Every suite that `make test-all` runs names a real script or module.

The runner starts each suite as a separate process, so a typo in a suite's path
or module name only shows when the suite runs. This test catches it without
running any suite.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import test_all  # noqa: E402 - the runner lives in scripts/, not on the path


def test_suite_names_are_unique() -> None:
    names = [suite.name for suite in test_all.SUITES]
    assert len(names) == len(set(names)), names


def test_every_suite_names_a_real_script_or_module() -> None:
    for suite in test_all.SUITES:
        args = suite.argv[1:]
        if args[:1] == ("-m",):
            module = args[1]
            found = importlib.util.find_spec(module) is not None
            assert found, f"{suite.name}: no module named {module}"
        else:
            script = ROOT / args[0]
            assert script.is_file(), f"{suite.name}: no script at {script}"
