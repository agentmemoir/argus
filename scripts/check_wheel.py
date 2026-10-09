#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check that a built wheel carries the package and only the package.

`make wheel` builds the wheel and runs this. The package is the `src/argus`
folder, and hatchling maps it to the distribution path `argus/`, so this
compares the wheel against that folder: a file the package does not ship fails
the target, and so does a package file missing from the wheel.

    python3 scripts/check_wheel.py dist/argus-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "argus"
#: The distribution path the folder maps to, from pyproject.toml's `packages`.
PACKAGE = "argus"
#: Local artifacts hatchling keeps out of the wheel: caches, bytecode, dotfiles.
SKIP_PARTS = frozenset({"__pycache__"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd")


def package_files() -> set[str]:
    """The files the package folder ships, as package-relative paths."""
    files: set[str] = set()
    for path in PACKAGE_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PACKAGE_DIR)
        if any(part in SKIP_PARTS or part.startswith(".") for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        files.add(rel.as_posix())
    return files


def main(argv: list[str]) -> int:
    wheel = Path(argv[0]) if argv else None
    if wheel is None or not wheel.is_file():
        print(f"no wheel at {wheel}; run `uv build --wheel` first", file=sys.stderr)
        return 2
    if not PACKAGE_DIR.is_dir():
        print(f"no package folder at {PACKAGE_DIR}", file=sys.stderr)
        return 2

    expected = package_files()
    with zipfile.ZipFile(wheel) as archive:
        shipped = {
            name[len(PACKAGE) + 1:]
            for name in archive.namelist()
            if name.startswith(f"{PACKAGE}/") and not name.endswith("/")
        }

    problems = [f"development file in the wheel: {rel}"
                for rel in sorted(shipped - expected)]
    problems += [f"package file missing from the wheel: {rel}"
                 for rel in sorted(expected - shipped)]
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        return 1
    print(f"wheel: {len(shipped)} package files, {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
