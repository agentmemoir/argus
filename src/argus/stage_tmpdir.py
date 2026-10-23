#!/usr/bin/env python3
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The stage temporary folder: a folder that runs the files a stage starts.

Some hosts mount the default temporary folder `noexec`. A stage runs a Bun-based
agent CLI that loads its native library from the temporary folder, so on such a
host the stage never starts. Argus gives each Glide run a private folder under
the run-state folder instead. This module creates that folder and probes it, so
the readiness check can say whether a stage can start here.

The driver runs it as `stage_tmpdir.py <folder>`. It prints one line with the
state and exits 0 only for `runs`. The states are kept apart on purpose:
`refused` means the kernel refused to start a file in the folder, and
`unprobed` means the probe could not run for another reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    # The driver runs this file as a script, so the package is not on the path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argus.exec_tempdir import (  # noqa: E402
    can_execute,
    refuses_execution,
)


def probe_state(folder: Path) -> str:
    """Return `runs`, `refused`, or `unprobed` for a folder."""
    if can_execute(str(folder)):
        return "runs"
    if refuses_execution(str(folder)):
        return "refused"
    return "unprobed"


def main(argv: list[str]) -> int:
    """Create the folder, probe it, and print one line with the state."""
    folder = Path(argv[1])
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"stage temp dir: {folder} (unprobed)")
        print(
            f"cause: the folder cannot be created ({exc.strerror or exc}). "
            "action: make the run-state folder writable, then run --check again"
        )
        return 1
    state = probe_state(folder)
    print(f"stage temp dir: {folder} ({state})")
    if state == "runs":
        return 0
    if state == "refused":
        print(
            "cause: the run-state folder is on a mount that refuses to run files. "
            "action: set driver.runs to a folder on a mount that allows it, "
            "then run --check again"
        )
    else:
        print(
            "cause: the stage folder probe could not run. "
            "action: make the run-state folder writable, then run --check again"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
