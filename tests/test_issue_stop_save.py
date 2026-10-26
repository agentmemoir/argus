# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""An issue run that stops early leaves no edits for the next branch cut (#288).

The launch-fault halt runs for real and calls the real issue checkpoint, which
commits and pushes the edits on the run's own branch. The next issue's branch
cut then finds a clean checkout, with no hand step.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from argus.gitsync.git import git
from argus.gitsync.issue_modes import mode_issue_branch
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

# The gitsync entry point with this test's settings, which live in-process.
GITSYNC = """import json, os, sys
from argparse import Namespace
from argus import conventions
from argus.gitsync.issue_modes import issue_mode
from argus.gitsync.modes import result_exit_code
from argus.gitsync.selftest_fixture import _configure_self_test
settings = json.loads(os.environ["TEST_SETTINGS"])
conventions.configure({"commands.git": "git"})
_configure_self_test(settings.pop("root"), settings.pop("private"))
conventions.override(settings)
argv = sys.argv[2:]
def flag(name):
    return argv[argv.index(name) + 1]
out = issue_mode(Namespace(mode=flag("--mode"), root=flag("--root"), phase=flag("--phase")))
print(json.dumps(out))
raise SystemExit(result_exit_code(out))
"""

HALT = """set -euo pipefail
for name in lib-driver-halt-record lib-driver-launchfault; do . "$TEST_DRIVER/$name.sh"; done
python() {
  case "$1" in
    */gitsync/main.py) "$TEST_PYTHON" "$TEST_GITSYNC" "$@" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
PYTHON=python ARGUS=/argus REPO="$TEST_ROOT" RUN_DIR="$TEST_RUN"
HALT="$RUN_DIR/halted" HALT_RECORD="$RUN_DIR/halted.json" CLAIM="$RUN_DIR/claim"
ARGUS_CONFIG_ARGS=() work_kind=issue
log() { printf '%s\\n' "$*" >>"$RUN_DIR/events"; }
ts() { printf fixture-time; }
notify() { :; }
tmux_set_title() { :; }
claim_read_fields() { return 1; }
launchfault_halt "$TEST_NUMBER" tasks/issue.md "$RUN_DIR" config_error 2 "" ""
"""


def stop_early(publication: Publication, tmp_path: Path, number: int) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    gitsync = tmp_path / "gitsync.py"
    gitsync.write_text(GITSYNC, encoding="utf-8")
    settings = {
        "root": str(publication.root),
        "private": str(publication.private),
        "board.branch": "fixture-claims",
        "board.issues-file": "issue-claims.json",
        "issues.repo": "example/project",
        "issues.task-dir": str(publication.private / "tasks"),
    }
    env = dict(
        os.environ,
        TEST_DRIVER=str(DRIVER),
        TEST_PYTHON=sys.executable,
        TEST_GITSYNC=str(gitsync),
        TEST_ROOT=str(publication.root),
        TEST_RUN=str(run),
        TEST_NUMBER=str(number),
        TEST_SETTINGS=json.dumps(settings),
    )
    subprocess.run(["bash", "-c", HALT], env=env, check=True, timeout=60)
    return run


def test_an_early_stop_saves_the_edits_so_the_next_cut_needs_no_hand_step(
    publication: Publication, tmp_path: Path
) -> None:
    _token, task = publication.claim(1234567)
    branch = mode_issue_branch(publication.root, 1234567, task)["branch"]
    # The incident: the branch had no upstream and one edit staged.
    assert git(publication.root, "branch", "--unset-upstream")[0] == 0
    assert git(publication.remote, "update-ref", "-d", f"refs/heads/{branch}")[0] == 0
    (publication.root / "code.txt").write_text("half a fix\n", encoding="utf-8")
    assert git(publication.root, "add", "code.txt")[0] == 0

    run = stop_early(publication, tmp_path, 1234567)

    assert "state=config_error" in (run / "halted").read_text()
    assert git(publication.root, "status", "--porcelain", "--", "code.txt")[1] == ""
    pushed = git(publication.remote, "show", f"{branch}:code.txt")
    assert pushed[1] == "half a fix", pushed
    _token, other = publication.claim(1234568, "next-fix")
    cut = mode_issue_branch(publication.root, 1234568, other)
    assert cut["ok"], cut
    assert cut["branch"] == "issue/1234568-next-fix"


def test_a_save_that_cannot_push_halts_with_a_named_state(
    publication: Publication, tmp_path: Path
) -> None:
    _token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    assert git(publication.root, "branch", "--unset-upstream")[0] == 0
    assert git(publication.root, "config", "branch.main.remote", "nowhere")[0] == 0
    (publication.root / "code.txt").write_text("half a fix\n", encoding="utf-8")

    run = stop_early(publication, tmp_path, 1234567)

    assert "state=PUSH_FAILED" in (run / "halted").read_text()
    record = json.loads((run / "halted.json").read_text())
    assert "not saved on its branch" in json.dumps(record)


def test_another_issue_branch_is_left_alone(
    publication: Publication, tmp_path: Path
) -> None:
    _token, task = publication.claim(1234567)
    mode_issue_branch(publication.root, 1234567, task)
    (publication.root / "code.txt").write_text("not this run's\n", encoding="utf-8")

    stop_early(publication, tmp_path, 7654321)

    assert git(publication.root, "status", "--porcelain")[1] == "?? code.txt"
