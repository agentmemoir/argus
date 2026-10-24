#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Make a folder that Argus owns when it is missing, and say so in one line.

The driver calls this for its run-state folder and the driver state folder inside
it. Nothing else is made here: a checkout that Argus does not own, such as the
board or the phases repository, must already exist, and the readiness check says
so when it does not.

Run by the driver as `owned_folder.py <label> <folder>`. A folder that is already
there prints nothing. A folder that was made prints one line naming it, on stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_owned_folder(label: str, folder: Path) -> str:
    """Make `folder` when it is missing, and return the line that says so.

    Returns an empty string when the folder was already there. Raises OSError when
    the folder cannot be made, for example when a file sits at its path.
    """
    if folder.is_dir():
        return ""
    folder.mkdir(parents=True)
    return f"argus: created {label} {folder}"


def main(argv: list[str]) -> int:
    """Run the check for one folder and return the exit status."""
    label, folder = argv[1], Path(argv[2])
    try:
        line = ensure_owned_folder(label, folder)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        print(
            f"argus: {label} {folder} is missing and cannot be created: {reason}",
            file=sys.stderr,
        )
        return 1
    if line:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
