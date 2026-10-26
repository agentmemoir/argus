# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An idle tick logs what changed, and an empty queue still gets its merge check."""

import os
import subprocess
import time
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

# The libraries under test, sourced into a shell that has stubbed the few
# functions that would reach git, GitHub or the board.
PRELUDE = """\
set -euo pipefail
export PYTHON=python3 REPO=/repo RUN_DIR="$FAKE/state" LOG="$FAKE/log" DRY_RUN=0
export ARGUS="$FAKE/argus" WORK_SOURCE=phases work_kind=phase
ARGUS_CONFIG_ARGS=(--config c.jsonc)
mkdir -p "$RUN_DIR"
source "$DRIVER/lib-driver-log.sh"
source "$DRIVER/lib-driver-select.sh"
source "$DRIVER/lib-driver-select-real.sh"
source "$DRIVER/lib-driver-group.sh"
"""

SELECTION = """\
tidy_published_checkout() { :; }
bind_selection_kind() { :; }
sync_branch_args() { SYNC_BRANCH_ARGS=(); }
confirm_pending_reviews() { echo "$1" >"$FAKE/merge-check"; PENDING_PUBLISHED=0; }
coordination_select() { printf '%s' "$ANSWER"; return "$RC"; }
selection_rc=0 _DRIVE_RC=""
_drive_real_selection
echo "state=$selection_state drive_rc=${_DRIVE_RC:-none}" >"$FAKE/result"
"""


def run(tmp_path: Path, script: str, **env: str) -> None:
    base = {"FAKE": str(tmp_path), "DRIVER": str(DRIVER), "PATH": os.environ["PATH"]}
    subprocess.run(["bash", "-c", PRELUDE + script], env=base | env, check=True)


def fake_sync(tmp_path: Path) -> None:
    """A checkout sync that succeeds, so the tick reaches the end of the selection."""
    main = tmp_path / "argus" / "gitsync" / "main.py"
    main.parent.mkdir(parents=True)
    main.write_text("import sys\nsys.exit(0)\n")


def log_lines(tmp_path: Path) -> list[str]:
    log = tmp_path / "log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_repeated_line_is_logged_once_and_a_changed_one_again(
    tmp_path: Path,
) -> None:
    run(
        tmp_path,
        'log_changed k "same"; log_changed k "same"; log_changed k "other"; '
        'log_changed k "same"; log_changed j "same"',
    )
    assert [line.split(" ", 1)[1] for line in log_lines(tmp_path)] == [
        "same",
        "other",
        "same",
        "same",
    ]


def test_a_suppressed_repeat_still_touches_the_log(tmp_path: Path) -> None:
    run(tmp_path, 'log_changed k "same"')
    log = tmp_path / "log"
    os.utime(log, (0, 0))
    run(tmp_path, 'log_changed k "same"')
    assert len(log_lines(tmp_path)) == 1
    assert time.time() - log.stat().st_mtime < 60


def test_an_empty_queue_is_not_a_refusal_or_a_second_merge_check(
    tmp_path: Path,
) -> None:
    answer = '{"state": "NO_PHASE", "awaiting_review": [{"phase": "100926"}]}'
    fake_sync(tmp_path)
    run(tmp_path, SELECTION, ANSWER=answer, RC="1")
    assert (tmp_path / "result").read_text().strip() == "state=NO_PHASE drive_rc=none"
    assert not (tmp_path / "merge-check").exists()
    assert "unavailable" not in "".join(log_lines(tmp_path))


def test_selection_does_not_repeat_completed_maintenance(
    tmp_path: Path,
) -> None:
    answer = '{"state": "NO_PHASE", "awaiting_review": [{"phase": "100926"}]}'
    fake_sync(tmp_path)
    # Publication is settled before this function; selection must not repeat it.
    recorded = SELECTION.replace("PENDING_PUBLISHED=0; }", "PENDING_PUBLISHED=1; }")
    run(tmp_path, recorded, ANSWER=answer, RC="1")
    assert (tmp_path / "result").read_text().strip() == "state=NO_PHASE drive_rc=none"
    assert not (tmp_path / "merge-check").exists()
    assert "unavailable" not in "".join(log_lines(tmp_path))


def test_a_selector_that_prints_no_state_still_refuses_the_launch(
    tmp_path: Path,
) -> None:
    run(tmp_path, SELECTION, ANSWER="", RC="1")
    assert (tmp_path / "result").read_text().strip() == "state= drive_rc=1"
    assert not (tmp_path / "merge-check").exists()
    assert "refusing to launch (next_phase rc=1)" in "".join(log_lines(tmp_path))


FAKE_BOARD = """\
import pathlib, sys
here = pathlib.Path(sys.argv[0]).resolve().parents[2]
sys.exit(int((here / "board-rc").read_text()))
"""

RENEW = """\
tmux_busy() { return 0; }
CLAIM="$FAKE/claim" GROUPFILE="$FAKE/group" PHASE_MACHINE_ID=m
printf '100030\\trsv-1\\t3\\n' >"$CLAIM"
board() { printf '%s' "$1" >"$FAKE/board-rc"; claim_renew; }
"""


def test_a_renewal_after_a_failure_is_logged_again(tmp_path: Path) -> None:
    board = tmp_path / "argus" / "board" / "reservations.py"
    board.parent.mkdir(parents=True)
    board.write_text(FAKE_BOARD)
    run(tmp_path, RENEW + "board 0; board 0; board 1; board 0; board 0")
    outcomes = [
        line.split(": ", 1)[1].split(" for ")[0] for line in log_lines(tmp_path)
    ]
    assert outcomes == [
        "renewed the claim",
        "could not renew the claim",
        "renewed the claim",
    ]
