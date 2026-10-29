#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Make the pipeline's git hooks ready in the work checkout before a Glide run.

The pipeline's pre_run hook refuses a run while the checkout's hooks are missing or
stale. This runs that hook first. When it refuses and the configuration names a hook
install command (`commands.hooks-install`, an argument list, never a shell string),
it backs up the checkout's hooks folder, runs the command, and runs the hook again.
Each outcome is one line. A refusal that remains is named with its action and check.

The driver runs it as `hooks_setup.py <run-state folder> --repo <checkout>` followed
by the configuration arguments. It exits 0 when the hook accepts the checkout, and 1
when a person has to act.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

if __name__ == "__main__":
    # The driver runs this file as a script, so the package is not on the path yet.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argus import conventions  # noqa: E402
from argus.prerun_hook import main as prerun_main  # noqa: E402

INSTALL_TIMEOUT_S = 600
GIT_TIMEOUT_S = 60
ACTION = (
    "action: set commands.hooks-install to the argument list that installs this "
    "project's git hooks, then run --check again"
)
CHECK = "check: the pre_run hook line reads accepted"


def pre_run_report(pipeline: Path, repo: Path) -> tuple[bool, str]:
    """Run the pipeline's pre_run hook in the checkout; return (accepted, its text)."""
    text = io.StringIO()
    previous = Path.cwd()
    os.chdir(repo)
    try:
        with contextlib.redirect_stdout(text):
            status = prerun_main(["--pipeline", str(pipeline)])
    finally:
        os.chdir(previous)
    return status == 0, text.getvalue().strip()


def first_line(text: str) -> str:
    """The first line of a hook report, which names the refusal."""
    lines = text.splitlines()
    return lines[0] if lines else "no message from the pre_run hook"


def hooks_folder(repo: Path) -> Path | None:
    """The folder git reads hooks from for this checkout, honouring core.hooksPath."""
    done = subprocess.run(  # noqa: S603 - a fixed argument list
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0 or not done.stdout.strip():
        return None
    path = Path(done.stdout.strip())
    return path if path.is_absolute() else repo / path


def back_up(hooks: Path | None, run_dir: Path) -> Path | None:
    """Copy the hooks folder into the run state before anything replaces it."""
    if hooks is None or not hooks.is_dir():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    destination = run_dir / "hooks-backup" / stamp
    shutil.copytree(hooks, destination)
    return destination


def ensure_hooks(
    run_dir: Path, repo: Path, pipeline: Path, install: list[str] | None
) -> tuple[int, str]:
    """Return (status, one line) after making the hooks ready for this checkout."""
    accepted, report = pre_run_report(pipeline, repo)
    if accepted:
        return 0, "hooks: present (the pre_run hook accepted the work checkout)"
    refusal = first_line(report)
    if not install:
        return 1, (
            "Needs You. The pre_run hook refuses the work checkout, and "
            "commands.hooks-install is not set, so Argus cannot install the hooks. "
            f"{ACTION}. {CHECK}. Refusal: {refusal}"
        )
    hooks = hooks_folder(repo)
    backup = back_up(hooks, run_dir)
    done = subprocess.run(  # noqa: S603 - the argument list from configuration
        install,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=INSTALL_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        return 1, (
            f"Needs You. The hook install command exited {done.returncode}: "
            f"{' '.join(install)}. Action: run it by hand to see why, then run "
            f"--check again. {CHECK}."
        )
    accepted, report = pre_run_report(pipeline, repo)
    if not accepted:
        return 1, (
            "Needs You. The hook install command ran, but the pre_run hook still "
            f"refuses: {first_line(report)}. {ACTION}. {CHECK}."
        )
    kept = f"backup {backup}" if backup else "no earlier hooks folder to back up"
    return 0, (f"hooks: installed with {' '.join(install)} in {hooks}; {kept}")


def main(argv: list[str]) -> int:
    """Run the check for the configuration on the command line."""
    run_dir = Path(argv[1])
    rest = argv[2:]
    if len(rest) < 2 or rest[0] != "--repo":
        print("hooks: refused (the driver did not name the work checkout)")
        return 1
    repo = Path(rest[1])
    conventions.configure_from_cli(rest[2:])
    pipeline = Path(conventions.setting("pipeline"))
    install = conventions.setting("commands.hooks-install", None)
    status, line = ensure_hooks(run_dir, repo, pipeline, install or None)
    print(line)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
