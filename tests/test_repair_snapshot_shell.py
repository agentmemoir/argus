# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver shell runs the real issue snapshot: a failed read holds, then excludes.

Nothing in these tests stubs the snapshot snippet or the listing. The gh binary
is missing, so every issue read fails for real with "gh is not installed".
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

from config_issue_fixture import issue_document
from driver_maintenance_support import shell

ISSUE = "000007"
BODY = """
REPAIR_PIPELINE="$TEST_ROOT/repair.yaml"; : >"$REPAIR_PIPELINE"
REPAIR_ATTEMPTS=3; REPAIR_WAIT=600; WORK_SOURCE=issues
notify() { log "NOTIFY $*"; }
check_stall() { :; }
_drive
"""


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _layout(root: Path, attempts: list[float] | None) -> None:
    # The public checkout holds a nested private checkout, as publication requires.
    _git_init(root)
    board = root / "board"
    _git_init(board)
    tasks = board / "tasks"
    tasks.mkdir()
    task = tasks / f"issue-{ISSUE}-fix.md"
    task.write_text("# fix\n", encoding="utf-8")
    run = board / "runs" / f"issue-{ISSUE}"
    run.mkdir(parents=True)
    token = {
        "issue": "7",
        "machine_id": "worker-one",
        "reservation_id": "rsv-7",
        "generation": 1,
    }
    (run / "reservation.json").write_text(json.dumps(token), encoding="utf-8")
    manifest = {
        "reservation_id": "rsv-7",
        "generation": 1,
        "path": f"board/tasks/issue-{ISSUE}-fix.md",
        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
    }
    (run / "task-snapshot.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "pipeline.yaml").write_text("", encoding="utf-8")
    (root / "machine.id").write_text("worker-one\n", encoding="utf-8")
    document = issue_document()
    document["repo-root"] = str(root)
    document["pipeline"] = str(root / "pipeline.yaml")
    document["phases"]["repo"] = str(root)
    document["machine"]["id-file"] = str(root / "machine.id")
    document["commands"]["gh"] = str(root / "no-gh")
    document["driver"].pop(
        "runs", None
    )  # unset: the run folder is in the private checkout
    document["board"]["repo"] = str(board)
    document["issues"]["task-dir"] = str(tasks)
    (root / "fixture.json").write_text(json.dumps(document), encoding="utf-8")
    if attempts is not None:
        count = {"attempts": attempts, "reason": "gh is not installed"}
        (run / "read-fault.json").write_text(json.dumps(count), encoding="utf-8")


def _run(root: Path, attempts: list[float] | None) -> list[str]:
    _layout(root, attempts)
    shell(root, BODY)
    return (root / "events").read_text(encoding="utf-8").splitlines()


def test_a_failed_read_with_no_count_holds_new_work(tmp_path: Path) -> None:
    events = _run(tmp_path, None)
    assert any("unread for [7]" in e for e in events), events
    assert "SELECT" not in events
    assert not any(e.startswith("NOTIFY") for e in events)


def test_the_third_failed_read_notifies_once_and_selection_goes_on(
    tmp_path: Path,
) -> None:
    now = time.time()
    events = _run(tmp_path, [now - 1200, now - 600])
    assert sum(e.startswith("NOTIFY Needs You") for e in events) == 1, events
    assert "SELECT" in events
    assert not any("unread for" in e for e in events)
