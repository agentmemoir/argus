# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The installed command: hand every argument to the shell driver beside this file.

`bin/argus` runs from a source checkout and this module runs from an installed
package, so both end in the same `execvp`. They are kept apart on purpose: the
guard check patches `bin/argus` to prove the entry point is covered, and a shared
helper would move that anchor out of the file it is aimed at.
"""

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """Replace this process with the shell driver, keeping args and status."""
    args = list(sys.argv[1:] if argv is None else argv)
    driver = Path(__file__).resolve().parent / "driver" / "phase-driver.sh"
    os.execvp("bash", ["bash", str(driver), *args])


if __name__ == "__main__":
    main()
