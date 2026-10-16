#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Exercise an installed source copy against an unrelated, local-only project.

The project's layout is deliberately unguessable: the phases sits at
``foo/bar/phases`` and the board in a separate checkout. Every value the tick
needs comes from one configuration file, so nothing here relies on a folder name
or on the caller's environment.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from argus.exec_tempdir import exec_tempdir
from entrypoint_exercise import exercise
from entrypoint_fixture import ARGUS


def main():
    try:
        with exec_tempdir(purpose="the installed Argus command") as scratch:
            base = Path(scratch)
            # Nothing beside this copy is a project tool or a vendored engine.
            package = base / "tools/source-checkout"
            shutil.copytree(
                ARGUS,
                package,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".git", ".venv", "dist", "target"
                ),
            )
            exercise(package, base)
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}")
        print("entrypoint self-test: FAIL")
        return 1
    print("entrypoint self-test: pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
