# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Which argus source files a driver self-test case can reach, read from the code.

A case reaches the Python modules its shell files start (``$ARGUS/<path>.py`` and
``python -m argus.<name>``), and every module those import, transitively. The
result places a changed Python file under the cases that reach it, so the quick
tier runs only those cases. The import scan reads ``import`` and ``from ... import``
statements, including ones inside functions, so a lazy import is still counted.
A dynamic import (``importlib`` with a computed name) is not seen; the full tier
on main is the backstop for that.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

SHELL_PY = re.compile(r"\$ARGUS/([A-Za-z0-9_/]+)\.py\b|argus/([A-Za-z0-9_/]+)\.py\b")
SHELL_MOD = re.compile(r"-m\s+argus\.([A-Za-z0-9_.]+)")


def _module_file(root: Path, name: str) -> Path | None:
    """The source file for a dotted module name under src/, or None if absent."""
    base = root / "src" / Path(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _parents(name: str) -> list[str]:
    """The packages an import of ``name`` runs first, outermost first."""
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


@cache
def _imports(path: Path, name: str) -> frozenset[str]:
    """Dotted names a module imports, including the submodule a from-import names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    is_package = path.name == "__init__.py"
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                here = name.split(".") if is_package else name.split(".")[:-1]
                keep = len(here) - (node.level - 1)
                base = ".".join(here[:keep])
                prefix = ".".join(p for p in (base, node.module or "") if p)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            found.add(prefix)
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return frozenset(found)


def reachable(root: Path, entries: set[str]) -> set[str]:
    """Repository paths of every argus source file the entry modules reach."""
    seen: set[str] = set()
    files: set[str] = set()
    pending = list(entries)
    while pending:
        name = pending.pop()
        if name in seen or not name.startswith("argus"):
            continue
        seen.add(name)
        path = _module_file(root, name)
        if path is None:
            continue
        files.add(path.relative_to(root).as_posix())
        pending.extend(_parents(name))
        pending.extend(_imports(path, name))
    return files


def shell_entries(root: Path, shell_paths: list[str]) -> set[str]:
    """The argus modules that the given shell files start, by their file references."""
    names: set[str] = set()
    for rel in shell_paths:
        path = root / rel
        if not path.is_file() or path.suffix != ".sh":
            continue
        text = path.read_text(encoding="utf-8")
        for match in SHELL_PY.finditer(text):
            dotted = (match.group(1) or match.group(2)).replace("/", ".")
            names.add(f"argus.{dotted}")
        for match in SHELL_MOD.finditer(text):
            names.add(f"argus.{match.group(1)}")
    return {n for n in names if _module_file(root, n) is not None}


def case_reach(root: Path, case_files: dict[str, list[str]]) -> dict[str, set[str]]:
    """For each case, the argus source files it reaches. ``case_files`` maps a case to
    the repository paths its map lines name; the shell ones start Python entries."""
    reach: dict[str, set[str]] = {}
    for case, paths in case_files.items():
        entries = shell_entries(root, paths)
        for path in paths:
            if path.endswith(".py") and path.startswith("src/argus/"):
                dotted = path[len("src/") : -3].replace("/", ".")
                entries.add(dotted)
        reach[case] = reachable(root, entries)
    return reach
