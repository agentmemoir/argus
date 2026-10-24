# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Example post_step hook for an issue pipeline.

glide runs this from the repository root after each step, with GLIDE_RUN_DIR
set. It runs the checkpoint mode, then merges the base into the checked-out
issue branch with base-sync, then runs the open-pr mode with the run's pull
request body. It exits 1 when a mode refuses or degrades, or when base-sync
leaves a text conflict or fails, so glide, which gates post_step, does not
record the step as complete.
"""

import json
import os
import subprocess
import sys

GITSYNC = [sys.executable, "-m", "argus.gitsync.main", "--kind", "issue"]
BASE_SYNC = [sys.executable, "-m", "argus.gitsync.base_sync_cli"]


def run_mode(args: list[str]) -> bool:
    """Run one gitsync mode; True only when it passed with no warning."""
    proc = subprocess.run(
        [*GITSYNC, *args], capture_output=True, text=True, check=False
    )
    print(proc.stdout.strip() or proc.stderr.strip())
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    return proc.returncode == 0 and result.get("ok") is True and "warning" not in result


def run_base_sync() -> bool:
    """Merge the base into the checked-out branch; True only when it is clean.

    Exit 0 means no conflict or a clean merge that was pushed. Exit 3 means a
    text conflict is left in progress, and that is not a pass.
    """
    head = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = head.stdout.strip()
    if head.returncode != 0 or not branch:
        print("base-sync: no branch is checked out, so nothing was merged")
        return False
    proc = subprocess.run(
        [*BASE_SYNC, "--repo", ".", "--pr-branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def main() -> int:
    run_dir = os.environ["GLIDE_RUN_DIR"]
    # The base is merged before the pull request is opened (belief 10).
    ok = (
        run_mode(["--mode", "checkpoint"])
        and run_base_sync()
        and run_mode(["--mode", "open-pr", "--body-file", f"{run_dir}/pr-body.md"])
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
