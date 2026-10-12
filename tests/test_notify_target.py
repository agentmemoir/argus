# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A notice goes to the configured target as written, and to the fallback the same way."""

import os
import stat
import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

SCRIPT = """\
set -euo pipefail
export LOG="$FAKE/log" DRY_RUN=0 NOTIFY_DEAD=0 NOTIFY_BROKEN="$FAKE/broken" NOTIFY_DISABLED=0
export PROFILE=profile CMD_HERMES="$FAKE/hermes" DISABLE_NOTIFY= DISABLE_DISCORD=
export CHANNEL="$TARGET" FALLBACK="$BACKUP"
source "$DRIVER/lib-driver-log.sh"
source "$DRIVER/lib-driver-notify.sh"
ts() { echo now; }
notify "hello"
"""

# A hermes that records the target it was given and fails for the first target.
HERMES = """\
#!/bin/sh
echo "$@" >>"$(dirname "$0")/sent"
[ "$5" != "discord:1" ]
"""


def run(tmp_path: Path, target: str, backup: str = "") -> list[str]:
    hermes = tmp_path / "hermes"
    hermes.write_text(HERMES)
    hermes.chmod(hermes.stat().st_mode | stat.S_IXUSR)
    env = {
        "FAKE": str(tmp_path),
        "DRIVER": str(DRIVER),
        "TARGET": target,
        "BACKUP": backup,
        "PATH": os.environ["PATH"],
    }
    subprocess.run(["bash", "-c", SCRIPT], env=env, check=False)
    sent = tmp_path / "sent"
    return sent.read_text().splitlines() if sent.exists() else []


def test_the_configured_target_is_used_as_written(tmp_path: Path) -> None:
    assert run(tmp_path, "discord:123") == ["-p profile send --to discord:123 hello"]


def test_the_fallback_is_used_as_written_when_the_first_target_fails(
    tmp_path: Path,
) -> None:
    sent = run(tmp_path, "discord:1", backup="discord:2")
    assert sent[-1] == "-p profile send --to discord:2 hello"
    assert all("discord:discord" not in line for line in sent)
