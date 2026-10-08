#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Check that a built wheel carries the package and only the package.

`make wheel` builds the wheel and runs this. The expected contents are the files
in this folder that the packaging config ships: the project root is the package,
and pyproject.toml names the exclusion list, so this reads that same list rather
than keeping a second one. Both failures fail the target: a file the package
does not ship present in the wheel, and a file the package ships missing from it.

    python3 check_wheel.py dist/argus-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import fnmatch
import sys
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: The distribution path the folder maps to, from pyproject.toml's `sources`.
PACKAGE = "argus"
#: Not part of the package at any version: build output and bytecode.
ALWAYS_SKIP = ("dist/", "__pycache__/")
ALWAYS_SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd")


def excluded(rel: str, patterns: list[str]) -> bool:
    """True when a relative path is one of the packaging exclude patterns."""
    for pattern in patterns:
        stem = pattern.lstrip("/")
        if fnmatch.fnmatch(rel, stem) or rel == stem or rel.startswith(stem + "/"):
            return True
    return False


def package_files(patterns: list[str]) -> set[str]:
    """The files this folder ships, as package-relative paths."""
    files: set[str] = set()
    for path in HERE.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(HERE).as_posix()
        if rel.startswith(ALWAYS_SKIP) or "/__pycache__/" in rel:
            continue
        if path.suffix in ALWAYS_SKIP_SUFFIXES or excluded(rel, patterns):
            continue
        files.add(rel)
    return files


def main(argv: list[str]) -> int:
    wheel = Path(argv[0]) if argv else None
    if wheel is None or not wheel.is_file():
        print(f"no wheel at {wheel}; run `uv build --wheel` first", file=sys.stderr)
        return 2
    config = tomllib.loads((HERE / "pyproject.toml").read_text())
    patterns = config["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]

    expected = package_files(patterns)
    with zipfile.ZipFile(wheel) as archive:
        shipped = {
            name[len(PACKAGE) + 1:]
            for name in archive.namelist()
            if name.startswith(f"{PACKAGE}/") and not name.endswith("/")
        }

    problems = [f"development file in the wheel: {rel}" for rel in sorted(shipped - expected)]
    problems += [f"package file missing from the wheel: {rel}" for rel in sorted(expected - shipped)]
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        return 1
    print(f"wheel: {len(shipped)} package files, {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
