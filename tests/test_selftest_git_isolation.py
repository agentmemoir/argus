# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The readiness self-tests run git with a fixed configuration.

A host's global git config reaches every fixture repository the readiness
report's two self-tests build. A machine-wide ``core.hooksPath`` starts its hooks
inside each fixture git call, which made the readiness report several times
slower on the machine this was reported from, and a hook that fails makes both
self-tests fail for a reason that is not in Argus.

The hostile config below is that setting: a global ``core.hooksPath`` whose
``reference-transaction`` hook fails every git command that reads it. A self-test
that still reads the host's config cannot reach its verdict, so each one is run
the way the readiness report runs it, as its own process with that config in the
environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ARGUS = Path(__file__).resolve().parents[1]

#: Every git command that reads this hook fails.
FAILING_HOOK = "#!/bin/sh\nexit 1\n"

#: The two self-tests the readiness report runs, by the entry point and the
#: argument the report passes: `--self-test` for the selector, and the
#: `self-test` subcommand for the board tool, which has no such flag.
SELF_TESTS = (
    ("select/next_phase.py", "--self-test"),
    ("board/reservations.py", "self-test"),
)


def _hostile_global_config(exec_dir: Path) -> Path:
    """A global git config whose hooks fail, written where the host runs them.

    The hook needs its executable bit and the host has to really run it, so this
    comes from the ``exec_dir`` fixture and not from a bare temporary directory:
    on a ``noexec`` mount the kernel would refuse the hook and the check would
    pass for the wrong reason.
    """
    hooks = exec_dir / "hostile-hooks"
    hooks.mkdir()
    hook = hooks / "reference-transaction"
    hook.write_text(FAILING_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    config = exec_dir / "hostile-gitconfig"
    config.write_text(f"[core]\n\thooksPath = {hooks}\n", encoding="utf-8")
    return config


@pytest.mark.parametrize("entry, self_test_arg", SELF_TESTS)
def test_readiness_self_test_ignores_the_host_global_git_config(
    entry: str, self_test_arg: str, exec_dir: Path, tmp_path: Path
) -> None:
    """Each self-test must pass on a machine whose global git hooks fail.

    The run gets its own ``TMPDIR`` because the coordination self-test counts the
    scratch checkouts under the temporary directory it is given. A shared one
    would make that count read a concurrent run's checkouts.
    """
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    env = dict(
        os.environ,
        GIT_CONFIG_GLOBAL=str(_hostile_global_config(exec_dir)),
        GIT_CONFIG_NOSYSTEM="1",
        TMPDIR=str(scratch),
    )
    result = subprocess.run(
        [sys.executable, str(ARGUS / "src/argus" / entry), self_test_arg],
        cwd=ARGUS,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"{entry}:\n{result.stdout}\n{result.stderr}"
