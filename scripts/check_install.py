#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Prove the built wheel installs and runs in a clean environment.

`make install-check` builds the wheel and runs this. Only the built wheel and
the example configuration are used; nothing else from the checkout is on the
installed package's path:

* a fresh venv, created with `uv venv`, installs the wheel with `uv pip`;
* every module the wheel ships is imported;
* the shell driver sits beside the installed package;
* the installed `argus` console script passes the example readiness report
  (`--check`), with the example's stand-in `glide` first on PATH.

The example's run-state folder (`examples/runs`) is created when missing; it is
git-ignored.

    python3 scripts/check_install.py dist/argus-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

#: Runs inside the fresh venv: import every shipped module, then prove the
#: shell driver shipped beside the package. Importing every module is safe:
#: each does only in-memory work at import time, and the `_bootstrap` branch
#: fires only in direct script mode.
IMPORT_CHECK = """\
import importlib
import pkgutil
from pathlib import Path

import argus

names = sorted(m.name for m in pkgutil.iter_modules(argus.__path__))
for name in names:
    importlib.import_module(f"argus.{name}")
driver = Path(argus.__file__).resolve().parent / "phase-driver.sh"
if not driver.is_file():
    raise SystemExit(f"the driver did not ship beside the package: {driver}")
print(f"imported {len(names)} modules; driver: {driver.name}")
"""


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """Run a command with captured text output."""
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def main(argv: list[str]) -> int:
    wheel = Path(argv[0]) if argv else None
    if wheel is None or not wheel.is_file():
        print(f"no wheel at {wheel}; run `uv build --wheel` first", file=sys.stderr)
        return 2
    if shutil.which("uv") is None:
        print("uv is not on PATH; this check installs with uv", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="argus-install-check-") as tmp:
        venv = Path(tmp) / "venv"
        python = venv / "bin" / "python"
        for command in (
            ["uv", "venv", "--python", sys.executable, str(venv)],
            ["uv", "pip", "install", "--python", str(python), "--quiet", str(wheel)],
        ):
            result = run(command)
            if result.returncode != 0:
                print(f"failed: {' '.join(command)}", file=sys.stderr)
                print(result.stdout + result.stderr, file=sys.stderr)
                return 1

        result = run([str(python), "-c", IMPORT_CHECK])
        if result.returncode != 0:
            print("the installed package did not import cleanly:", file=sys.stderr)
            print(result.stdout + result.stderr, file=sys.stderr)
            return 1
        print(result.stdout.strip())

        (EXAMPLES / "runs").mkdir(exist_ok=True)
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(
            [str(venv / "bin"), str(EXAMPLES / "check-bin"), env.get("PATH", "")]
        )
        console = venv / "bin" / "argus"
        result = run(
            [
                str(console),
                "--config",
                str(EXAMPLES / "argus-config.jsonc"),
                "--check",
            ],
            env=env,
        )
        if result.returncode != 0:
            print(
                "the installed console script failed the example report:",
                file=sys.stderr,
            )
            print(result.stdout + result.stderr, file=sys.stderr)
            return 1
        print(f"console script: {console.name} --check exit 0")

    print(f"install check: {wheel.name} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
