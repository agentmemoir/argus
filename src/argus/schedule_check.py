#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether a tick schedule runs this configuration. Read only: Argus never edits a crontab.

The readiness check runs this as `schedule_check.py <config> <run-state folder>
<argus entry>`. It reads the current user's crontab with `crontab -l` and looks for
an active line that runs the configuration. When none does, it prints one Needs You
line with the exact line to install and the check that proves it worked. An
unreadable crontab is reported as unknown, never as a healthy schedule.

The result is printed, not turned into a failed check: the readiness report of a
machine without a schedule is still a true report, and only a person can install
the line.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

CRON_TIMES = "*/5 * * * *"
CRONTAB_TIMEOUT_S = 30


def read_crontab() -> tuple[str, str]:
    """Return (state, text): `read` with the text, `none`, or `unknown` with a reason."""
    try:
        done = subprocess.run(  # noqa: S603 - a fixed argument list
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=CRONTAB_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"crontab could not be run ({exc})"
    if done.returncode == 0:
        return "read", done.stdout
    if "no crontab" in (done.stderr or "").lower():
        return "none", ""
    return "unknown", f"crontab -l exited {done.returncode}"


def runs_config(text: str, config: Path) -> bool:
    """Whether an active line in `text` names `config` after --config."""
    wanted = config.resolve()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        for index, token in enumerate(tokens):
            value = None
            if token == "--config" and index + 1 < len(tokens):
                value = tokens[index + 1]
            elif token.startswith("--config="):
                value = token.partition("=")[2]
            if value is not None and Path(value).resolve() == wanted:
                return True
    return False


def exact_line(config: Path, run_dir: Path, entry: str) -> str:
    """The one crontab line that runs a tick for `config`, with absolute paths."""
    command = f"{entry} --config {config.resolve()}"
    if shutil.which("flock"):
        command = f"flock -n {run_dir.resolve() / 'cron.lock'} {command}"
    return f"{CRON_TIMES} {command}"


def report(config: Path, run_dir: Path, entry: str) -> str:
    """The one line the readiness report prints for the schedule."""
    state, text = read_crontab()
    if state == "unknown":
        return (
            f"schedule: unknown ({text}); Argus cannot tell whether a tick runs. "
            "action: make crontab -l readable for this user, then run --check again"
        )
    if state == "read" and runs_config(text, config):
        return "schedule: present (a crontab line runs this configuration)"
    line = exact_line(config, run_dir, entry)
    check = f"crontab -l | grep -F -- '--config {config.resolve()}'"
    return (
        "Needs You. No crontab line runs this configuration, so no tick starts. "
        f"Action: add this line with crontab -e: {line} "
        f"Check: {check} prints the line, then run --check again."
    )


def main(argv: list[str]) -> int:
    """Print the schedule line for the configuration named on the command line."""
    if len(argv) < 4 or not argv[1] or not argv[3]:
        print("schedule: unknown (no configuration file or entry point to name)")
        return 0
    print(report(Path(argv[1]), Path(argv[2]), argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
