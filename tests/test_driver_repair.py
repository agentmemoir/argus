# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The tick's CI repair step and the repair round's own worktree."""

import subprocess
from pathlib import Path

from driver_maintenance_support import shell

CLI = """
REPAIR_PIPELINE="$TEST_ROOT/repair.yaml"; : >"$REPAIR_PIPELINE"; REPAIR_ATTEMPTS=3; REPAIR_WAIT=600; PENDING_PUBLISHED=0
python_transport() {
  case "$1" in
    */gitsync/repair_cli.py)
      log "CLI $*"
      case "$*" in
        *--launched*) [ -z "${COUNT_FAILS:-}" ] || return 1; printf '{"action":"counted"}' ;;
        *) printf '%s' "$DECISION" ;;
      esac ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
notify() { log "NOTIFY $*"; }
"""


def _tick(tmp_path: Path, body: str) -> str:
    shell(
        tmp_path,
        CLI + body + '\nrepair_tick "$(pending_review_snapshot)"\n'
        'log "pending=$PENDING_PUBLISHED"\n',
    )
    return (tmp_path / "events").read_text(encoding="utf-8")


def test_a_started_round_is_counted_and_holds_new_work(tmp_path: Path) -> None:
    events = _tick(
        tmp_path,
        """DECISION='{"action":"repair","task":"/t.md"}'
repair_launch() { log "LAUNCH $*"; }""",
    )
    assert "LAUNCH 1 /t.md feature/fixture" in events
    assert "--launched" in events
    assert "pending=1" in events


def test_a_failed_launch_uses_its_attempt_before_it_fails(tmp_path: Path) -> None:
    events = _tick(
        tmp_path,
        """DECISION='{"action":"repair","task":"/t.md"}'
repair_launch() { log "LAUNCH $*"; return 1; }""",
    )
    assert events.index("--launched") < events.index("LAUNCH")
    assert "pending=0" in events


def test_a_round_that_cannot_be_counted_never_launches(tmp_path: Path) -> None:
    events = _tick(
        tmp_path,
        """DECISION='{"action":"repair","task":"/t.md"}'
COUNT_FAILS=1
repair_launch() { log "LAUNCH $*"; }""",
    )
    assert "--launched" in events
    assert "LAUNCH" not in events
    assert "pending=0" in events


def test_an_escalation_is_a_notice(tmp_path: Path) -> None:
    events = _tick(
        tmp_path, """DECISION='{"action":"escalate","message":"still red"}'"""
    )
    assert "NOTIFY still red" in events
    assert "pending=0" in events


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )


def _checkout(tmp_path: Path, ignore: str) -> Path:
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    _git("init", "-q", "--bare", str(origin))
    _git("clone", "-q", str(origin), str(repo))
    (repo / ".gitignore").write_text(ignore, encoding="utf-8")
    _git("-C", str(repo), "add", ".gitignore")
    _git("-C", str(repo), "commit", "-qm", "base")
    _git("-C", str(repo), "push", "-q", "origin", "HEAD:refs/heads/feature/fixture")
    (repo / "state/runs/.driver").mkdir(parents=True)
    return repo


WORKTREE = """
REPO="$TEST_ROOT/repo"; WF="$REPO/state/runs"; RUN_DIR="$WF/.driver"; CMD_GIT=git
notify() { log "NOTIFY $*"; }
"""


def test_a_round_gets_a_worktree_and_a_later_tick_removes_it(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, "/state\n")
    out = shell(
        tmp_path,
        WORKTREE
        + """
before="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
repair_worktree_add 7 feature/fixture && echo added
[ -L "$RUN_DIR/repair-worktree-7/state" ] && echo linked
[ "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)" = "$before" ] && echo unmoved
repair_worktree_prune
[ -e "$RUN_DIR/repair-worktree-7" ] || echo removed
""",
    )
    assert out.split() == ["added", "linked", "unmoved", "removed"]
    assert (
        "repair-worktree"
        not in subprocess.run(
            ["git", "-C", str(repo), "worktree", "list"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )


def test_a_link_git_would_commit_refuses_the_round(tmp_path: Path) -> None:
    _checkout(tmp_path, "other\n")
    out = shell(
        tmp_path,
        WORKTREE
        + """
repair_worktree_add 7 feature/fixture || echo refused
[ -e "$RUN_DIR/repair-worktree-7" ] || echo removed
""",
    )
    assert out.split() == ["refused", "removed"]
    events = tmp_path / "repo/state/runs/.driver/events"
    assert "NOTIFY Repair 7 did not start" in events.read_text(encoding="utf-8")
