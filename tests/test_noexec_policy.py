# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Executable test stubs must come from a probed directory.

A host may mount its temporary storage ``noexec``. A test that makes its own
temporary directory with ``tempfile.mkdtemp`` and writes an executable into it,
or marks a ``tmp_path`` file executable and runs it, then fails on a permission
error that has nothing to do with the code, or, worse, passes because a real
command answered instead of the stub. ``argus.exec_tempdir`` finds a directory
the host really runs; this scan keeps a new test from bypassing it. It runs as
``make noexecscan``.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

# A file that writes an executable into a directory the caller proved it runs
# names the helper or the fixture that hands it one. `entrypoint_exercise` runs
# inside that scratch, so it is exempt from the naming rule.
HELPER_NAMES = ("exec_dir", "exec_tempdir", "make_exec_dir", "refuses_execution")
SKIP = {Path(__file__).resolve(), TESTS / "entrypoint_exercise.py"}

# `chmod` with an executable bit: a symbolic exec bit, or a numeric mode whose
# digits include an exec bit (1, 3, 5, or 7).
EXEC_CHMOD = re.compile(r"chmod\(.*(?:S_I(?:EXEC|XUSR|XGRP|XOTH)|0o[0-7]*[1357][0-7]*)")


def _offenders() -> list[str]:
    """Return one ``file:line: reason`` line per banned occurrence."""
    found: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path in SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        names_helper = any(name in text for name in HELPER_NAMES)
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{path.relative_to(TESTS)}:{number}"
            if "tempfile.mkdtemp(" in line:
                found.append(f"{where}: tempfile.mkdtemp, use a probed directory")
            if EXEC_CHMOD.search(line) and not names_helper:
                found.append(
                    f"{where}: chmod sets an executable bit without a probed "
                    "directory (exec_dir / exec_tempdir / make_exec_dir)"
                )
    return found


def test_tests_use_only_probed_temporary_storage() -> None:
    """No test creates its own temp dir, or marks a temp file executable, alone."""
    offenders = _offenders()
    assert not offenders, "\n".join(offenders)
