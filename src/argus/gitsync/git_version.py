# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The git version check that `argus --check` runs.

base-sync tests a merge with `git merge-tree --write-tree`, which git 2.38 added.
An older git is refused by the readiness check, with one line that names the
version found and the version needed, so no repair round or issue run finds it
late.
"""

import argparse
import re
import subprocess
import sys

NEEDED = (2, 38)
_NEEDS = "2.38 or newer is needed for merge-tree --write-tree"
_VERSION = re.compile(r"git version (\d+\.\d+(?:\.\d+)*)")


def classify(output: str) -> tuple[bool, str]:
    """Whether the text `git --version` printed is new enough, with its one line."""
    match = _VERSION.search(output)
    if match is None:
        return (
            False,
            f"git version: REFUSED (git --version printed no version; {_NEEDS})",
        )
    found = match.group(1)
    major, minor = (int(part) for part in found.split(".")[:2])
    if (major, minor) >= NEEDED:
        return True, f"git version: accepted ({found})"
    return False, f"git version: REFUSED (found {found}; {_NEEDS})"


def probe(git: str) -> tuple[bool, str]:
    """Run `git --version` as an argument list, then classify what it printed."""
    try:
        done = subprocess.run(
            [git, "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, f"git version: REFUSED (git --version could not be run; {_NEEDS})"
    if done.returncode != 0:
        return False, (
            f"git version: REFUSED (git --version exited {done.returncode}; {_NEEDS})"
        )
    return classify(done.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse a git older than 2.38.")
    parser.add_argument("git", nargs="?", default="git", help="the git command to test")
    accepted, line = probe(parser.parse_args(argv).git)
    print(line)
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
