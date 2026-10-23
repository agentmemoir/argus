# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the checkout lock tests: a live holder and a shell probe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from driver_fence_fixture import git

LIB = Path(__file__).resolve().parents[1] / "src/argus/driver/lib-driver-checkout.sh"

PRELUDE = """
set -u
REPO="$TEST_REPO"; PYTHON="$TEST_PYTHON"; ARGUS_CONFIG_FILE="$TEST_CONFIG"
. "$TEST_LIB"
"""


def make_checkout(root: Path) -> Path:
    """A git checkout with one commit, under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "fixture")
    (root / "README").write_text("fixture\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "fixture")
    return root


def _env(repo: Path, config: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        TEST_REPO=str(repo),
        TEST_PYTHON=sys.executable,
        TEST_CONFIG=str(config),
        TEST_LIB=str(LIB),
    )
    return env


def start_holder(repo: Path, config: Path) -> subprocess.Popen[str]:
    """Start a process that takes the checkout lock and holds it until stdin closes.

    It stands in for a phase process. Closing its stdin ends it, and the kernel
    releases the lock with it.
    """
    body = PRELUDE + (
        "driver_checkout_take || { echo REFUSED; exit 3; }\necho TAKEN\nread -r _\n"
    )
    proc = subprocess.Popen(
        ["bash", "-c", body],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_env(repo, config),
    )
    assert proc.stdout is not None and proc.stdin is not None
    line = proc.stdout.readline().strip()
    assert line == "TAKEN", f"the holder did not take the lock: {line!r}"
    return proc


def stop_holder(proc: subprocess.Popen[str]) -> None:
    assert proc.stdin is not None
    proc.stdin.close()
    proc.wait(timeout=30)


def run_as(repo: Path, config: Path, body: str) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a new shell that loads the checkout lock as ``config``."""
    return subprocess.run(
        ["bash", "-c", PRELUDE + body],
        env=_env(repo, config),
        capture_output=True,
        text=True,
        timeout=60,
    )
