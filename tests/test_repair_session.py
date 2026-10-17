# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver's repair hook decides what the tick does, and a repair run never halts the line."""

import json
import os
import subprocess
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"
# The glide stand-in runs from the repository, never from temporary storage.
GLIDE_STUB = Path(__file__).resolve().parent / "fixtures" / "fake_glide.sh"

PRELUDE = """\
set -euo pipefail
export PYTHON=python3 REPO="$FAKE/repo" RUN_DIR="$FAKE/state" LOG="$FAKE/log" DRY_RUN=0
export ARGUS="$FAKE/argus" REPAIR_PIPELINE="$FAKE/repair.yaml"
export CMD_GIT=git PIDFILE="$FAKE/pid" HALT="$FAKE/halt" DRIVER_STUB_COORDINATION=
ARGUS_CONFIG_ARGS=(--config c.jsonc)
mkdir -p "$RUN_DIR" "$REPO"
git -C "$REPO" init -q
source "$DRIVER/lib-driver-log.sh"
source "$DRIVER/lib-driver-select.sh"
source "$DRIVER/lib-driver-repair.sh"
tmux_set_title() { echo "$*" >>"$FAKE/titles"; }
notify() { echo "$*" >>"$FAKE/notices"; }
repair_launch() { echo "$*" >"$FAKE/launched"; }
"""

# The Python step: prints the answer in `answer.json`, or nothing at all.
MAIN = """\
import pathlib, sys
here = pathlib.Path(sys.argv[0]).resolve().parents[2]
with (here / "calls").open("a") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
answer = here / "answer.json"
sys.stdout.write(answer.read_text() if answer.exists() else "")
"""


def setup(tmp_path: Path, answer: dict[str, object] | None, glide_rc: int = 0) -> None:
    main = tmp_path / "argus" / "gitsync" / "main.py"
    main.parent.mkdir(parents=True)
    main.write_text(MAIN)
    if answer is not None:
        (tmp_path / "answer.json").write_text(json.dumps(answer))
    (tmp_path / "glide-rc").write_text(str(glide_rc))


def run(tmp_path: Path, script: str, **env: str) -> int:
    base = {
        "FAKE": str(tmp_path),
        "DRIVER": str(DRIVER),
        "PATH": os.environ["PATH"],
        "CMD_GLIDE": str(GLIDE_STUB),
    }
    done = subprocess.run(["bash", "-c", PRELUDE + script], env=base | env, check=False)
    return done.returncode


def read(tmp_path: Path, name: str) -> list[str]:
    path = tmp_path / name
    return path.read_text().splitlines() if path.exists() else []


def test_a_repair_answer_launches_one_run_and_ends_the_tick(tmp_path: Path) -> None:
    setup(
        tmp_path,
        {
            "state": "REPAIR",
            "id": "7-1",
            "task": "/t/task.md",
            "branch": "b",
            "base": "abc123",
            "nonce": "7-1-1",
            "summary": "repair REPAIR: x",
        },
    )
    assert run(tmp_path, "repair_due") == 0
    assert read(tmp_path, "launched") == ["7-1 /t/task.md b abc123 7-1-1"]
    assert "--mode repair" in read(tmp_path, "calls")[0]


def test_a_hold_ends_the_tick_and_is_logged_once(tmp_path: Path) -> None:
    setup(
        tmp_path, {"state": "HOLD", "summary": "repair HOLD: checks are not finished"}
    )
    assert run(tmp_path, "repair_due; repair_due") == 0
    assert not (tmp_path / "launched").exists()
    assert len(read(tmp_path, "log")) == 1


def test_a_pull_request_given_up_lets_the_tick_go_on_and_notifies_once(
    tmp_path: Path,
) -> None:
    setup(
        tmp_path,
        {"state": "EXHAUSTED", "notify": True, "summary": "repair EXHAUSTED: x"},
    )
    assert run(tmp_path, "repair_due") == 1
    assert read(tmp_path, "notices") == ["repair EXHAUSTED: x"]
    setup_quiet = {
        "state": "EXHAUSTED",
        "notify": False,
        "summary": "repair EXHAUSTED: x",
    }
    (tmp_path / "answer.json").write_text(json.dumps(setup_quiet))
    assert run(tmp_path, "repair_due") == 1
    assert read(tmp_path, "notices") == ["repair EXHAUSTED: x"]


def test_nothing_blocked_or_unanswered_stops_the_tick(tmp_path: Path) -> None:
    setup(tmp_path, {"state": "BLOCKED", "summary": "repair BLOCKED: dirty"})
    assert run(tmp_path, "repair_due") == 1
    (tmp_path / "answer.json").write_text(json.dumps({"state": "NONE"}))
    assert run(tmp_path, "repair_due") == 1
    (tmp_path / "answer.json").unlink()
    assert run(tmp_path, "repair_due") == 1
    assert "no answer" in " ".join(read(tmp_path, "log"))


def test_without_a_repair_pipeline_the_hook_does_nothing(tmp_path: Path) -> None:
    setup(tmp_path, {"state": "REPAIR", "id": "7-1", "task": "t", "branch": "b"})
    assert run(tmp_path, "REPAIR_PIPELINE=; repair_due") == 1
    assert not (tmp_path / "calls").exists()


def test_a_finished_run_pushes_and_never_halts(tmp_path: Path) -> None:
    # The repair pipeline commits on the branch; the checkpoint's push moves it.
    setup(tmp_path, {"ok": True, "action": "pushed"})
    run(tmp_path, "run_repair_session 7-1 /t/task.md b")
    assert (tmp_path / "glide-args").read_text().split() == [
        "run",
        "--pipeline",
        str(tmp_path / "repair.yaml"),
        "--input",
        "repair_id=7-1",
        "--input",
        "repair_task=/t/task.md",
    ]
    assert "--mode checkpoint" in read(tmp_path, "calls")[0]
    log = " ".join(read(tmp_path, "log"))
    assert "pushed to b" in log
    assert "pushed nothing" not in log
    assert not (tmp_path / "halt").exists() and not (tmp_path / "pid").exists()


def test_a_failed_run_pushes_nothing_and_never_halts(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True}, glide_rc=3)
    (tmp_path / "repo").mkdir()
    code = run(
        tmp_path, 'echo edit >"$REPO/x.txt"; run_repair_session 7-1 /t/task.md b'
    )
    assert code == 0
    assert not (tmp_path / "calls").exists()
    assert not (tmp_path / "halt").exists() and not (tmp_path / "pid").exists()
    assert "uncommitted edits" in " ".join(read(tmp_path, "notices"))


def test_a_run_that_pushed_nothing_is_not_read_as_a_push(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "nothing-to-push"})
    run(tmp_path, "run_repair_session 7-1 /t/task.md b")
    log = " ".join(read(tmp_path, "log"))
    assert "the run pushed nothing to b" in log
    assert "pushed to b" not in log


def commit(tmp_path: Path, name: str) -> str:
    """Commit one file in the fixture checkout and return the commit's id."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / name).write_text("x\n")
    ident = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", str(repo), *ident, "add", name], check=True)
    subprocess.run(
        ["git", "-C", str(repo), *ident, "commit", "-q", "-m", name], check=True
    )
    rev = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return rev.stdout.strip()


def test_a_check_over_the_changed_scope_keeps_its_answer(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    run(
        tmp_path,
        f"export REPAIR_VERIFY=false\nrun_repair_session 7-1 /t/task.md b {base}",
    )
    folder = tmp_path / "state" / "repair" / "7-1"
    assert (folder / "verify.rc").read_text().strip() == "1"
    assert (folder / "verify.paths").read_text().split() == ["change.txt"]
    assert (folder / "verify.command").read_text().strip() == "false"
    assert "the changed scope fails" in " ".join(read(tmp_path, "log"))


def test_the_check_reads_the_changed_paths_from_the_environment(
    tmp_path: Path,
) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    run(
        tmp_path,
        "export REPAIR_VERIFY='"
        'printf "%s|%s\\n" "$ARGUS_REPAIR_BASE" "$ARGUS_REPAIR_CHANGED" >"$FAKE/seen"'
        "'\n"
        f"run_repair_session 7-1 /t/task.md b {base}",
    )
    assert (tmp_path / "seen").read_text().splitlines() == [f"{base}|change.txt"]
    assert (tmp_path / "state" / "repair" / "7-1" / "verify.rc").exists()


def test_no_command_or_no_change_runs_no_check(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True})
    base = commit(tmp_path, "before.txt")
    run(tmp_path, f"run_repair_session 7-1 /t/task.md b {base}")
    assert not (tmp_path / "state" / "repair" / "7-1" / "verify.rc").exists()

    run(
        tmp_path,
        f"export REPAIR_VERIFY=true\nrun_repair_session 7-2 /t/task.md b {base}",
    )
    assert not (tmp_path / "state" / "repair" / "7-2" / "verify.rc").exists()
    assert "the repair changed no path" in " ".join(read(tmp_path, "log"))


# A repair round prepares its check before the push and answers it after.
#
# The push is the Python step, so the stand-in below reads the round's folder at
# the moment the push runs: it is a shell function, because `$PYTHON` is called
# by name and nothing here may run from temporary storage.
PUSH_PROBE = """\
push_probe() {
  case " $* " in
    *" --mode checkpoint "*) ls "$RUN_DIR/repair/7-1" >"$FAKE/before-push" ;;
  esac
  python3 "$@"
}
export PYTHON=push_probe
"""


def test_the_check_is_prepared_before_the_push_and_answered_after(
    tmp_path: Path,
) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    run(
        tmp_path,
        f"export REPAIR_VERIFY=true\n{PUSH_PROBE}"
        f"run_repair_session 7-1 /t/task.md b {base} nonce123",
    )
    folder = tmp_path / "state" / "repair" / "7-1"
    assert read(tmp_path, "before-push") == [
        "verify.command",
        "verify.id",
        "verify.paths",
    ]
    assert (folder / "verify.id").read_text().strip() == "nonce123"
    assert (folder / "verify.rc").read_text().strip() == "0"


def test_a_push_that_carried_nothing_clears_the_prepared_check(
    tmp_path: Path,
) -> None:
    setup(tmp_path, {"ok": True, "action": "nothing-to-push"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    run(
        tmp_path,
        f"export REPAIR_VERIFY=true\nrun_repair_session 7-1 /t/task.md b {base} n1",
    )
    folder = tmp_path / "state" / "repair" / "7-1"
    left = ["verify.id", "verify.paths", "verify.command", "verify.log", "verify.rc"]
    assert not [name for name in left if (folder / name).exists()]
    assert "cleared the prepared scope check" in " ".join(read(tmp_path, "log"))


def test_the_limiter_stopping_the_check_is_recorded(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    script = (
        'command() { case "${1:-}" in -v) [ "${2:-}" = timeout ] ;; '
        '*) builtin command "$@" ;; esac; }\n'
        "timeout() { return 124; }\n"
        "export REPAIR_VERIFY=true REPAIR_VERIFY_TIMEOUT=30\n"
        f"run_repair_session 7-1 /t/task.md b {base} n1\n"
    )
    run(tmp_path, script)
    folder = tmp_path / "state" / "repair" / "7-1"
    assert (folder / "verify.rc").read_text().strip() == "124"


def test_a_missing_limiter_logs_and_runs_the_check_unbounded(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    script = (
        "command() { return 1; }\n"
        "export REPAIR_VERIFY=true REPAIR_VERIFY_TIMEOUT=30\n"
        "export REPAIR_VERIFY='echo ran >\"$FAKE/ran\"'\n"
        f"run_repair_session 7-1 /t/task.md b {base} n1\n"
    )
    run(tmp_path, script)
    folder = tmp_path / "state" / "repair" / "7-1"
    assert (tmp_path / "ran").exists()
    assert (folder / "verify.rc").read_text().strip() == "0"
    assert "runs unbounded" in " ".join(read(tmp_path, "log"))


def test_gtimeout_is_used_when_timeout_is_absent(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    script = (
        'command() { case "${1:-}" in -v) [ "${2:-}" = gtimeout ] ;; '
        '*) builtin command "$@" ;; esac; }\n'
        'gtimeout() { echo "$1" >"$FAKE/limit"; shift; "$@"; }\n'
        "export REPAIR_VERIFY=true REPAIR_VERIFY_TIMEOUT=45\n"
        "export REPAIR_VERIFY='echo ran >\"$FAKE/ran\"'\n"
        f"run_repair_session 7-1 /t/task.md b {base} n1\n"
    )
    run(tmp_path, script)
    assert (tmp_path / "limit").read_text().strip() == "45"
    assert (tmp_path / "ran").exists()


def test_the_nonce_reaches_the_session(tmp_path: Path) -> None:
    setup(tmp_path, {"ok": True, "action": "pushed"})
    base = commit(tmp_path, "before.txt")
    commit(tmp_path, "change.txt")
    run(
        tmp_path,
        f"export REPAIR_VERIFY=true\nrun_repair_session 7-1 t b {base} nonce123",
    )
    folder = tmp_path / "state" / "repair" / "7-1"
    assert (folder / "verify.id").read_text().strip() == "nonce123"
