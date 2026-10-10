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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
# Let this run as `python3 scripts/check_install.py` as well as under `uv run`.
sys.path.insert(0, str(ROOT / "src"))

#: Runs inside the fresh venv: import every shipped module, then prove the
#: shell driver shipped beside the package. Importing every module is safe:
#: each does only in-memory work at import time, and the `_bootstrap` branch
#: fires only in direct script mode.
#:
#: The walk is over the files, not over `pkgutil.iter_modules`: the role
#: folders (`board/`, `gitsync/`, ...) are PEP 420 namespace packages with no
#: `__init__.py`, and `iter_modules` skips a directory that has none, so a walk
#: built on it would import four files and call the package checked.
IMPORT_CHECK = """\
import importlib
from pathlib import Path

import argus

root = Path(argus.__file__).resolve().parent
names = []
for path in sorted(root.rglob("*.py")):
    rel = path.relative_to(root).with_suffix("")
    if rel.name == "__init__" or "__pycache__" in rel.parts:
        continue
    names.append(".".join(("argus", *rel.parts)))
for name in names:
    importlib.import_module(name)
driver = root / "driver" / "phase-driver.sh"
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

    from argus.exec_tempdir import make_exec_dir

    # The clean venv's console script has to be executable, and a host whose
    # default temporary directory is `noexec` refuses it, so use the bundled
    # helper that finds a directory permitting execution.
    scratch = make_exec_dir()
    if not scratch:
        print(
            "no temporary directory on this host permits running the installed "
            "console script",
            file=sys.stderr,
        )
        return 2
    try:
        tmp = Path(scratch)
        venv = tmp / "venv"
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
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"install check: {wheel.name} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
