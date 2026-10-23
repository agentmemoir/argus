# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Example post_step hook for an issue pipeline.

glide runs this from the repository root after each step, with GLIDE_RUN_DIR
set. It runs the checkpoint mode, then the open-pr mode with the run's pull
request body. It exits 1 when a mode refuses or degrades, so glide, which gates
post_step, does not record the step as complete.
"""

import json
import os
import subprocess
import sys

GITSYNC = [sys.executable, "-m", "argus.gitsync.main", "--kind", "issue"]


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


def main() -> int:
    run_dir = os.environ["GLIDE_RUN_DIR"]
    steps = [
        ["--mode", "checkpoint"],
        ["--mode", "open-pr", "--body-file", f"{run_dir}/pr-body.md"],
    ]
    return 0 if all(run_mode(step) for step in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
