# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A run-state commit that fails is reported as degraded, not as a clean sync (issue #174).

A degraded step keeps ``ok`` true, so the line keeps running and the next tick
retries. The summary and the driver log must still say that the run state did
not land, because the exit code is 0 either way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import git as gitmod
from argus.gitsync import runstate
from argus.gitsync.git import summarize

DRIVER = Path(__file__).resolve().parents[1] / "src" / "argus" / "driver"
WARNING = "commit of run state failed (git exit 128); the next tick retries"

PRELUDE = """\
set -euo pipefail
export DRY_RUN=0 REPO="$ROOT" RUN_DIR="$ROOT/runs" LOG="$ROOT/driver.log"
ARGUS="$ROOT/argus"
REAL_PYTHON="$TEST_PYTHON"
ARGUS_CONFIG_ARGS=(--config c.jsonc)
GITSYNC_KIND_ARGS=()
mkdir -p "$RUN_DIR"
. "$DRIVER/lib-driver-log.sh"
. "$DRIVER/lib-driver-run.sh"
python_transport() {
  if [ "$1" = "-c" ]; then
    "$REAL_PYTHON" "$@"
    return $?
  fi
  printf '%s' "$GITSYNC_OUT"
  return "$GITSYNC_RC"
}
PYTHON=python_transport
"""


def _run_clean(tmp_path: Path, payload: dict[str, object]) -> list[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DRIVER": str(DRIVER),
        "ROOT": str(tmp_path),
        "TEST_PYTHON": sys.executable,
        "GITSYNC_OUT": json.dumps(payload),
        "GITSYNC_RC": "0",
    }
    done = subprocess.run(
        ["bash", "-c", PRELUDE + "gitsync_clean 7\n"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    log_path = tmp_path / "driver.log"
    return (
        log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    )


def test_a_degraded_clean_is_logged_as_not_landed(tmp_path: Path) -> None:
    lines = _run_clean(
        tmp_path, {"ok": True, "action": "continued", "warning": WARNING}
    )
    assert any(
        line.endswith(f"phase 7 closing run state did not land: {WARNING}")
        for line in lines
    )


def test_a_landed_clean_logs_no_degraded_line(tmp_path: Path) -> None:
    lines = _run_clean(tmp_path, {"ok": True, "action": "committed-and-pushed"})
    assert not any("did not land" in line for line in lines)


def _repo(tmp_path: Path) -> Path:
    def run(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    run(tmp_path, "init", "-q", "--bare", str(remote))
    run(tmp_path, "init", "-q", "-b", "main", str(repo))
    run(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    run(repo, "remote", "add", "origin", str(remote))
    run(repo, "push", "-q", "-u", "origin", "main")
    ledger = repo / "runs" / "phase-000001" / "ledger"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("row\n", encoding="utf-8")
    return repo


@pytest.fixture
def failed_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Run the run-state commit with git's commit step failing, as on a host with no identity."""
    conventions.configure({"commands.git": "git"})
    real_git = gitmod.git

    def fake_git(repo: object, *args: str, **kwargs: object) -> tuple[int, str, str]:
        if args[:1] == ("commit",):
            return (
                128,
                "",
                (
                    "Author identity unknown\n"
                    "fatal: unable to auto-detect email address "
                    "(got 'alice@build-host.(none)')\n"
                ),
            )
        return real_git(repo, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runstate, "git", fake_git)
    return runstate._commit_and_push_run_state(
        _repo(tmp_path), "closing run state", pathspec="runs/phase-000001"
    )


def test_a_failed_run_state_commit_is_not_summarized_as_ok(
    failed_commit: dict[str, object],
) -> None:
    summary = summarize([failed_commit], "clean")
    assert summary.startswith("clean sync DEGRADED")
    assert "commit of run state failed" in summary


def test_a_failed_run_state_commit_names_no_machine(
    failed_commit: dict[str, object],
) -> None:
    warning = str(failed_commit["warning"])
    assert "git exit 128" in warning
    assert "alice" not in warning
    assert "build-host" not in warning
