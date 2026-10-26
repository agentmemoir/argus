# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
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
repair_remote_head() { printf 'abc123'; }
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
    assert "--head abc123" in events
    assert "pending=1" in events


def test_a_pull_request_whose_head_cannot_be_read_is_skipped(tmp_path: Path) -> None:
    events = _tick(
        tmp_path,
        """DECISION='{"action":"repair","task":"/t.md"}'
repair_remote_head() { :; }
repair_launch() { log "LAUNCH $*"; }""",
    )
    assert "repair_cli" not in events
    assert "LAUNCH" not in events


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


def test_a_commit_the_remote_lacks_is_kept_even_with_a_tracking_ref(
    tmp_path: Path,
) -> None:
    _checkout(tmp_path, "/state\n")
    out = shell(
        tmp_path,
        WORKTREE
        + """
repair_worktree_add 7 feature/fixture
wt="$RUN_DIR/repair-worktree-7"
git -C "$wt" -c user.name=t -c user.email=t@t commit -q --allow-empty -m edit
git -C "$REPO" update-ref refs/remotes/origin/stale "$(git -C "$wt" rev-parse HEAD)"
repair_worktree_remove "$wt"
[ -n "$(git -C "$REPO" for-each-ref refs/argus/repair-backup)" ] && echo kept
""",
    )
    assert out.split() == ["kept"]


def test_a_commit_the_remote_has_is_not_kept(tmp_path: Path) -> None:
    _checkout(tmp_path, "/state\n")
    out = shell(
        tmp_path,
        WORKTREE
        + """
repair_worktree_add 7 feature/fixture
wt="$RUN_DIR/repair-worktree-7"
git -C "$wt" -c user.name=t -c user.email=t@t commit -q --allow-empty -m edit
git -C "$wt" push -q origin HEAD:refs/heads/feature/fixture
repair_worktree_remove "$wt"
[ -z "$(git -C "$REPO" for-each-ref refs/argus/repair-backup)" ] && echo none
""",
    )
    assert out.split() == ["none"]


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


def test_an_issue_tick_without_the_repair_pipeline_skips_the_snapshot(
    tmp_path: Path,
) -> None:
    shell(
        tmp_path,
        """WORK_SOURCE=issues; REPAIR_PIPELINE="$TEST_ROOT/missing.yaml"
issue_review_snapshot() { log "SNAPSHOT"; }
repair_tick() { log "TICK"; }
reconcile_group_ready() { :; }
reconcile_pending_reviews
""",
    )
    events = (tmp_path / "events").read_text(encoding="utf-8")
    assert "SNAPSHOT" not in events and "TICK" not in events
    assert "missing.yaml" in events


def test_a_hostile_branch_name_is_only_an_argument(tmp_path: Path) -> None:
    _checkout(tmp_path, "/state\n")
    hostile = 'x"$(touch_PWNED)"'
    out = shell(
        tmp_path,
        WORKTREE
        + f"""
git -C "$REPO" push -q origin 'HEAD:refs/heads/{hostile}'
head="$(git -C "$REPO" rev-parse HEAD)"
[ "$(repair_remote_head '{hostile}')" = "$head" ] && echo found
[ -z "$(repair_remote_head 'feature/*')" ] && echo nomatch
[ -e "$TEST_ROOT/touch_PWNED" ] && echo injected
echo done
""",
    )
    assert out.split() == ["found", "nomatch", "done"]


def test_a_commit_only_on_a_pull_ref_is_not_kept_as_pushed(tmp_path: Path) -> None:
    _checkout(tmp_path, "/state\n")
    out = shell(
        tmp_path,
        WORKTREE
        + """
repair_worktree_add 7 feature/fixture
wt="$RUN_DIR/repair-worktree-7"
git -C "$wt" -c user.name=t -c user.email=t@t commit -q --allow-empty -m edit
git -C "$wt" push -q origin HEAD:refs/pull/7/head
repair_worktree_remove "$wt"
[ -n "$(git -C "$REPO" for-each-ref refs/argus/repair-backup)" ] && echo kept
""",
    )
    assert out.split() == ["kept"]


def test_check_names_a_missing_repair_pipeline_and_does_not_fail(
    tmp_path: Path,
) -> None:
    out = shell(
        tmp_path,
        """. "$TEST_DRIVER/lib-driver-check.sh"
REPAIR_PIPELINE="$TEST_ROOT/missing.yaml"; check_repair_pipeline; echo "rc=$?"
""",
    )
    assert "MISSING: " in out and "missing.yaml" in out
    assert out.rstrip().endswith("rc=0")


def test_check_is_silent_when_the_repair_pipeline_exists(tmp_path: Path) -> None:
    out = shell(
        tmp_path,
        """. "$TEST_DRIVER/lib-driver-check.sh"
: >"$TEST_ROOT/repair.yaml"; REPAIR_PIPELINE="$TEST_ROOT/repair.yaml"; check_repair_pipeline
""",
    )
    assert "MISSING" not in out
