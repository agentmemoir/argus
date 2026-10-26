# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-test's teardown, on the path that keeps a fixture on purpose.

`selftest_teardown` removes the fixture tree and the tmux sessions a run made.
With `SELFTEST_KEEP_TMP=1` a failing run keeps the fixture instead, because the
reason it failed is usually in a file the teardown would delete. That branch
drops the ownership record as it goes, or the next run's sweep would read a pid
that is now gone and delete the very fixture the flag exists to keep.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ARGUS = Path(__file__).resolve().parents[1]
DRIVER = ARGUS / "bin/argus"

#: A git that always fails. The fixture root cannot be built, so the run stops
#: early with a non-zero status, which is the state the keep flag is for.
FAILING_GIT = "#!/bin/sh\nexit 1\n"


def test_a_kept_fixture_survives_without_its_record(exec_dir: Path) -> None:
    """The kept fixture is still there, and the record that would undo it is gone."""
    for tool in ("bash", "python3"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is needed to run the driver's self-test")

    shim = exec_dir / "bin"
    shim.mkdir()
    broken_git = shim / "git"
    broken_git.write_text(FAILING_GIT, encoding="utf-8")
    broken_git.chmod(0o755)

    with tempfile.TemporaryDirectory(prefix="ag-keep-", dir="/tmp") as base:
        env = dict(
            os.environ,
            PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}",
            TMPDIR=base,
            ARGUS_SELFTEST_BASES=base,
            SELFTEST_CASES="launch",
            SELFTEST_KEEP_TMP="1",
        )
        result = subprocess.run(
            [sys.executable, str(DRIVER), "--self-test"],
            cwd=ARGUS,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        kept = [
            line.split(": ", 1)[1]
            for line in result.stderr.splitlines()
            if line.startswith("selftest fixture kept: ")
        ]
        try:
            assert result.returncode != 0, "the run was supposed to fail"
            assert kept, f"no kept fixture was named:\n{result.stderr}"
            fixture = Path(kept[0])
            assert fixture.is_dir()
            assert not (fixture / "owner").exists(), "a kept fixture kept its record"
        finally:
            for path in kept:
                shutil.rmtree(path, ignore_errors=True)
