# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Tripwire: a new non-owned stop in the driver or gitsync fails this test.

The allowlist is keyed by file, rule, and the normalized stop line, never by a
line number, and every rule carries one owner reason. A stop that sits behind a
helper is attributed to the helper's callers. Add a stop and this test fails
until an owner reason is recorded here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src/argus"
DRIVER = ROOT / "driver"
GITSYNC = ROOT / "gitsync"

SHELL_RULES = {
    "exit_nonzero": re.compile(r"(?<![\w-])exit\s+([1-9][0-9]*)\b"),
    "return_nonzero": re.compile(r"(?<![\w-])return\s+([1-9][0-9]*)\b"),
    "drive_rc_nonzero": re.compile(r"_DRIVE_RC=([1-9][0-9]*)\b"),
    "drive_rc_zero_refusal": re.compile(r"_DRIVE_RC=0\b"),
    "halt_marker": re.compile(r">\s*\"\$HALT\""),
    "resume_refuse": re.compile(r"(?<![\w-])resume_refuse\b"),
}

PY_RULES = {
    "raise_fail": re.compile(r"raise\s+Fail\("),
    "raise_config": re.compile(r"raise\s+(ConfigError|ConfigException)\("),
    "raise_coordination": re.compile(
        r"raise\s+(CoordinationError|CoordinationUnavailable)\("
    ),
    "raise_fenced": re.compile(r"raise\s+ReservationFenced\("),
    "raise_busy": re.compile(r"raise\s+PublicationBusy\("),
    "blocked_call": re.compile(r"(?<![\w.])blocked\("),
    "ok_false": re.compile(r"\bok[\"']?\s*[:=]\s*False|ok=False"),
}

REFUSAL_WORDS = re.compile(
    r"refus|no launch|not runnable|halt|no new work|stop|block", re.IGNORECASE
)
HELPER_STOPS = {"resume_refuse", "driver_orphan_check", "on_stop"}

REASONS = {
    "exit_nonzero": "operator command, usage, broken install, or manual resume; not a tick stop",
    "return_nonzero": "a helper signal, an owned check, or the narrow lost-work stop at an unpreservable tidy",
    "drive_rc_nonzero": "an owned check, or the narrow lost-work stop at an unpreservable tidy",
    "drive_rc_zero_refusal": "a no-launch tick that warns and continues",
    "halt_marker": "operator stop, signal to a running job, orphaned run, or configuration halt",
    "resume_refuse": "owned reservation, running job, own state, or a usage error on --resume",
    "raise_fail": "glide or an owned run-state check; the caller repairs or retries",
    "raise_config": "start-time configuration or usage error; names the key and the fix",
    "raise_coordination": "owned reservation board; retries next tick",
    "raise_fenced": "owned reservation fence; retries next tick",
    "raise_busy": "owned publication lock; retries next tick",
    "blocked_call": "narrow lost-work stop, start-time configuration, or a publication retry",
    "ok_false": "retry or refusal signal for a caller that reports and continues",
}

_ALLOWLIST = r"""
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|driver_lock_take || { printf 'argus abandon: another driver instance holds the lock; try again\n' >&2; exit 1; }
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|driver_orphan_check || { printf 'argus abandon: a phase run is alive with no window (orphaned); not abandoning\n' >&2; exit 1; }
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1 ;;
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 2
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 2 ;;
    driver/lib-driver-check.sh	return_nonzero	fn:check|return 1
    driver/lib-driver-config.sh	exit_nonzero	fn:argus_init|*) printf 'argus: unknown driver %s; known drivers: phase, issues\n' "$DRIVER" >&2; exit 2 ;;
    driver/lib-driver-config.sh	exit_nonzero	fn:argus_init|exit 3
    driver/lib-driver-config.sh	return_nonzero	fn:load_config|if ! out="$(run_config "$@")"; then return 2; fi
    driver/lib-driver-config.sh	return_nonzero	fn:phases_dir_is_local|*) return 1 ;;
    driver/lib-driver-config.sh	return_nonzero	fn:resolve_python|[ -n "$candidate" ] && [ -x "$candidate" ] || return 1
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1
    driver/lib-driver-drive.sh	drive_rc_nonzero	fn:_drive_cut_branch|tidy_published_checkout || { _DRIVE_RC=1; return 0; }
    driver/lib-driver-drive.sh	halt_marker	fn:driver_orphan_check|[ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
    driver/lib-driver-drive.sh	halt_marker	fn:stop|printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|reconcile_pending_reviews || return 1
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|return 1
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_lock_take|flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_lock_wait|return 1
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_orphan_check|return 1
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	fn:_drive_dry_run|{ _DRIVE_RC=0; return 0; }
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_renew|reserve|fail) return 1 ;;
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_renew|return 1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_reserve|return 1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_reserve|return 1 ;;
    driver/lib-driver-group.sh	return_nonzero	fn:group_take_declared|return 1
    driver/lib-driver-halt-record.sh	halt_marker	fn:resume_refuse|printf 'resume refused: %s\n' "$reason" >"$HALT"
    driver/lib-driver-halt-record.sh	resume_refuse	fn:resume_refuse|resume_refuse() { # <phase> <reason>
    driver/lib-driver-halt-record.sh	return_nonzero	fn:halt_record_read|)" || return 1
    driver/lib-driver-halt-record.sh	return_nonzero	fn:resume_gate_reason|return 1
    driver/lib-driver-issues.sh	return_nonzero	fn:issue_preflight|return 1
    driver/lib-driver-launch.sh	drive_rc_nonzero	fn:_drive_launch||| { notify "${work_kind:-phase} $number failed to create tmux session '$SESSION'"; { _DRIVE_RC=1; return 0; }; }
    driver/lib-driver-launch.sh	drive_rc_nonzero	fn:_drive_launch||| { notify "${work_kind:-phase} $number failed to launch in tmux"; { _DRIVE_RC=1; return 0; }; }
    driver/lib-driver-log.sh	return_nonzero	fn:cap_last_byte|printf -v byte '%d' "'$last" || return 1
    driver/lib-driver-maintenance.sh	return_nonzero	fn:reconcile_pending_reviews|tidy_published_checkout || return 1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|driver_lock_take || { printf 'argus: another driver instance holds the lock; try again\n' >&2; exit 1; }
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|driver_orphan_check || { printf 'argus: a phase run is alive with no window (orphaned); the line is halted\n' >&2; exit 1; }
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 2 ;;
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$PREFLIGHT_REASON"
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$RESUME_REFUSAL"
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "no phase file found for phase $number"
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "the halt record $HALT_RECORD is missing or malformed"
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "the launch failed; see $RUN_DIR/driver.log"
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume_branch|return 1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1
    driver/lib-driver-run.sh	halt_marker	fn:on_stop|"${work_kind:-phase}" "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    driver/lib-driver-run.sh	halt_marker	fn:run_session|"${work_kind:-phase}" "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
    driver/lib-driver-run.sh	return_nonzero	fn:claim_read_fields|IFS= read -r line <"$1" || return 1
    driver/lib-driver-run.sh	return_nonzero	fn:claim_read_fields|[ -s "$1" ] || return 1
    driver/lib-driver-run.sh	return_nonzero	fn:tmux_busy|return 1
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	fn:_drive_choose_selection|{ _DRIVE_RC=0; return 0; }
    driver/lib-driver-select-real.sh	drive_rc_nonzero	fn:_drive_real_selection|{ _DRIVE_RC=1; return 0; }
    driver/lib-driver-select.sh	return_nonzero	fn:tidy_published_checkout|return 1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -eq 3 ] || { echo "usage: phase-driver.sh --session <N> <phase-file>" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'argus: %s needs a value\n' "$1" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -ge 2 ] || { echo "usage: phase-driver.sh abandon <NUMBER> [--reason TEXT]" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -le 1 ] || { echo "usage: phase-driver.sh --resume [NUMBER]" >&2; exit 2; }
    driver/phase-driver.sh	exit_nonzero	fn:<top>|exit 2
    driver/phase-driver.sh	exit_nonzero	fn:<top>|exit 3
    driver/phase-tmux-idle.sh	exit_nonzero	fn:<top>|CONFIG_OUT="$("$PYTHON" "$PKG_DIR/config/cli.py" "$@" --format shell)" || exit 2
    driver/phase-tmux-idle.sh	exit_nonzero	fn:<top>|exit 2
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_alive|[ "$dead" = "1" ] && return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_alive|wid="$(tmux_window_id)" || return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_interrupt|"$TMUX_BIN" send-keys -t "$wid" C-c 2>/dev/null || return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_interrupt|wid="$(tmux_window_id)" || return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_open|"$TMUX_BIN" new-window -d -t "$SESSION" -n "$TMUX_PHASE_WINDOW" -c "$REPO" "$launch" || return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_open|wid="$(tmux_window_id)" || return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_session_ensure|return 1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_session_ensure||| { log "failed to create tmux session '$SESSION'"; return 1; }
    gitsync/branch.py	blocked_call	fn:ensure_phase_branch|return blocked(
    gitsync/git.py	blocked_call	fn:blocked|def blocked(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    gitsync/git.py	raise_config	fn:git|raise ConfigError("git is not on PATH") from exc
    gitsync/group_publication.py	blocked_call	fn:_push_one|return blocked(
    gitsync/group_publication.py	blocked_call	fn:_push_one|return blocked({"repo": str(repo)}, why)
    gitsync/group_publication.py	raise_config	fn:_declared_group|raise ConfigError(
    gitsync/group_publication.py	raise_config	fn:_load_phase_reservation|raise ConfigError(f"cannot read phase reservation: {exc}") from exc
    gitsync/group_publication.py	raise_config	fn:_write_publication_receipt|raise ConfigError(f"phase run directory is missing: {run_dir}")
    gitsync/group_review.py	ok_false	fn:update_group_pull_request|"ok": False,
    gitsync/issue_modes.py	ok_false	fn:issue_mode|"ok": False,
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError(
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError("--phase is required with issue publish mode")
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError("issue branches do not accept phase groups")
    gitsync/issue_modes.py	raise_config	fn:mode_issue_branch|raise ConfigError("branch task filename differs from the frozen issue task")
    gitsync/issue_paths.py	raise_config	fn:issue_branch|raise ConfigError("issue task filename does not match the issue number")
    gitsync/issue_paths.py	raise_config	fn:issue_number|raise ConfigError(str(exc)) from exc
    gitsync/issue_paths.py	raise_config	fn:issue_scope|raise ConfigError("current issue has no valid frozen task snapshot")
    gitsync/issue_paths.py	raise_config	fn:issue_scope|raise ConfigError("issue data must be inside the private checkout")
    gitsync/issue_paths.py	raise_config	fn:load_issue_reservation|raise ConfigError("issue reservation file must be inside the current run")
    gitsync/issue_paths.py	raise_config	fn:load_issue_reservation|raise ConfigError("reservation does not belong to the current issue")
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError(
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError("issue publication needs a separate private checkout")
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError("public checkout tracks private issue data")
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError(str(exc)) from exc
    gitsync/main.py	ok_false	fn:main|"ok": False,
    gitsync/main.py	raise_config	fn:main|raise ConfigError(
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode publish")
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode published")
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode signals")
    gitsync/merge.py	blocked_call	fn:_merge|return blocked(
    gitsync/modes.py	ok_false	fn:mode_published|"ok": False,
    gitsync/paths.py	raise_config	fn:_phase_file_parts|raise ConfigError(
    gitsync/paths.py	raise_config	fn:_phase_file_parts|raise ConfigError(f"phase file carries no behaviour: {phases_name!r}")
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError(
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError("a group branch needs at least one member")
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError(f"not a phase number: {member!r}") from exc
    gitsync/paths.py	raise_config	fn:phase_branch_for|raise ConfigError(
    gitsync/pr_signal_reply.py	ok_false	fn:_refused|return {"ok": False, "state": "REFUSED", "reasons": [reason], "events": []}
    gitsync/pr_signals.py	ok_false	fn:mode_signals|return {"ok": False, "state": "UNAVAILABLE", "reasons": [reason], "events": []}
    gitsync/pr_signals.py	ok_false	fn:watch_pull_request|return {"ok": False, "state": "UNAVAILABLE", "reasons": [why], "events": []}
    gitsync/publish.py	blocked_call	fn:_commit_phase_receipt|return blocked(
    gitsync/publish_issue.py	ok_false	fn:fence|info, ok=False, state="RETRY", summary=summarize([clean], "publish")
    gitsync/publish_issue.py	ok_false	fn:fence|ok=False,
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="COORDINATION_UNAVAILABLE", error=str(exc))
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="FENCED", error=str(exc))
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="RETRY", summary=resync["summary"])
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="RETRY", summary=synced["summary"])
    gitsync/publish_issue.py	raise_config	fn:fence|raise ConfigError("frozen issue task does not belong to the current claim")
    gitsync/publish_issue.py	raise_config	fn:publish_issue|raise ConfigError("public checkout is not on the current issue branch")
    gitsync/publish_issue.py	raise_config	fn:publish_issue|raise ConfigError(why or "issue upstream names a different branch")
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,
    gitsync/publish_phase.py	ok_false	fn:publish_phase|outcome or {"ok": False, "mode": "publish", "phase": f"{int(phase):06d}"}
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError("publication requires phase-scoped run-state commit")
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError(f"invalid phase number: {phase!r}") from exc
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError(f"phase number out of range: {phase}")
    gitsync/runstate.py	blocked_call	fn:_commit_and_push_run_state|return blocked(
    gitsync/runstate.py	blocked_call	fn:commit_leftover_run_state|blocked(
    gitsync/runstate.py	ok_false	fn:commit_leftover_run_state|"ok": False,
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(f"invalid phase number: {phase!r}") from None
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(f"phase number out of range: {number}")
    gitsync/sync.py	blocked_call	fn:resync_integration|blocked_result = blocked(
    gitsync/sync.py	blocked_call	fn:sync_all|blocked({"repo": str(path), "mode": mode}, reason)
    gitsync/sync.py	ok_false	fn:sync_all|"ok": False,
    gitsync/tidy.py	blocked_call	fn:return_to_integration|return blocked(
"""


def _allowed() -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for line in _ALLOWLIST.strip().splitlines():
        rel, rule, anchor = line.strip().split("\t", 2)
        out.add((rel, rule, anchor))
    return out


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())[:200]


def _spans(text: str) -> list[tuple[str, int, int]]:
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", line)
        if match:
            starts.append((match.group(1), i))
    out: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else 10**9
        out.append((name, start, end))
    return out


def _py_spans(text: str) -> list[tuple[str, int, int]]:
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines()):
        match = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            starts.append((match.group(1), i))
    out: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else 10**9
        out.append((name, start, end))
    return out


def _owner(spans: list[tuple[str, int, int]], line_number: int) -> str:
    for name, start, end in spans:
        if start <= line_number < end:
            return name
    return "<top>"


def _callers(texts: dict[str, str], name: str) -> list[str]:
    found: list[str] = []
    for _rel, text in texts.items():
        for other, start, end in _spans(text):
            if other == name:
                continue
            body = "\n".join(text.splitlines()[start:end])
            if re.search(rf"(?<![\w-]){re.escape(name)}\b", body):
                found.append(other)
    return sorted(set(found))


def _scan(
    driver: Path = DRIVER, gitsync: Path = GITSYNC
) -> list[tuple[str, str, str, str, list[str]]]:
    findings: list[tuple[str, str, str, str, list[str]]] = []
    shell_texts = {
        f"driver/{path.name}": path.read_text()
        for path in sorted(driver.glob("*.sh"))
        if "selftest" not in path.name and path.name != "selftest-support.sh"
    }
    for rel, text in shell_texts.items():
        spans = _spans(text)
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#"):
                continue
            owner = _owner(spans, i)
            for rule, pattern in SHELL_RULES.items():
                if not pattern.search(line):
                    continue
                if rule == "drive_rc_zero_refusal":
                    window = " ".join(text.splitlines()[max(0, i - 2) : i + 1])
                    if not REFUSAL_WORDS.search(window):
                        continue
                # The owner function is part of the key: a new stop in a new
                # function is not covered by an allowlist row for the same line
                # text in another function.
                anchor = f"fn:{owner}|{_normalize(line)}"
                helper = owner if owner in HELPER_STOPS else ""
                callers = _callers(shell_texts, owner) if helper else []
                findings.append((rel, rule, anchor, helper, callers))
    py_texts = {
        f"gitsync/{path.name}": path.read_text()
        for path in sorted(gitsync.glob("*.py"))
        if "selftest" not in path.name
    }
    for rel, text in py_texts.items():
        spans = _py_spans(text)
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#"):
                continue
            owner = _owner(spans, i)
            for rule, pattern in PY_RULES.items():
                if pattern.search(line):
                    findings.append(
                        (rel, rule, f"fn:{owner}|{_normalize(line)}", "", [])
                    )
    return findings


def test_no_unowned_stop_appears() -> None:
    allowed = _allowed()
    unexpected = [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan()
        if (rel, rule, anchor) not in allowed
    ]
    assert not unexpected, (
        "a new stop path must be removed or allowlisted with an owner reason in "
        f"{__file__}: {unexpected}"
    )


def test_every_allowed_rule_has_an_owner_reason() -> None:
    for _rel, rule, _anchor in _allowed():
        assert rule in REASONS, f"allowlisted rule {rule!r} has no owner reason"


def test_a_helper_stop_names_its_callers() -> None:
    helpers = [f for f in _scan() if f[3] == "resume_refuse"]
    assert helpers, "the resume_refuse helper stop was not found"
    for _rel, _rule, _anchor, _helper, callers in helpers:
        assert "_drive_resume" in callers, callers


SHELL_INJECTIONS = {
    "exit_nonzero": "  exit 1",
    "return_nonzero": "  return 1",
    "drive_rc_nonzero": "  _DRIVE_RC=1",
    "drive_rc_zero_refusal": "  log 'refusing to launch'\n  _DRIVE_RC=0",
    "halt_marker": "  printf 'x\\n' >\"$HALT\"",
    "resume_refuse": '  resume_refuse "$target" "$reason"',
}

PY_INJECTIONS = {
    "raise_fail": "    raise Fail('x')",
    "raise_config": "    raise ConfigError('x')",
    "raise_coordination": "    raise CoordinationError('x')",
    "raise_fenced": "    raise ReservationFenced('x')",
    "raise_busy": "    raise PublicationBusy('x')",
    "blocked_call": "    return blocked(info, 'x')",
    "ok_false": "    return {'ok': False}",
}


def _unexpected(driver: Path, gitsync: Path) -> list[tuple[str, str, str]]:
    allowed = _allowed()
    return [
        (rel, rule, anchor)
        for rel, rule, anchor, _helper, _callers in _scan(
            driver=driver, gitsync=gitsync
        )
        if (rel, rule, anchor) not in allowed
    ]


def test_an_injected_stop_fails_the_tripwire(tmp_path: Path) -> None:
    driver = tmp_path / "driver"
    driver.mkdir()
    (driver / "lib-driver-new.sh").write_text("new_helper() { return 1; }\n")
    injected = _unexpected(driver, tmp_path)
    assert (
        "driver/lib-driver-new.sh",
        "return_nonzero",
        "fn:new_helper|new_helper() { return 1; }",
    ) in injected


@pytest.mark.parametrize("rule", sorted(SHELL_INJECTIONS))
def test_a_shell_stop_in_an_existing_file_fails(tmp_path: Path, rule: str) -> None:
    driver = tmp_path / "driver"
    driver.mkdir()
    source = (DRIVER / "lib-driver-run.sh").read_text()
    body = SHELL_INJECTIONS[rule]
    (driver / "lib-driver-run.sh").write_text(
        source + f"\nnew_helper() {{\n{body}\n}}\n"
    )
    injected = _unexpected(driver, tmp_path)
    assert any(found_rule == rule for _rel, found_rule, _anchor in injected), injected


@pytest.mark.parametrize("rule", sorted(PY_INJECTIONS))
def test_a_python_stop_in_an_existing_file_fails(tmp_path: Path, rule: str) -> None:
    gitsync = tmp_path / "gitsync"
    gitsync.mkdir()
    source = (GITSYNC / "merge.py").read_text()
    body = PY_INJECTIONS[rule]
    (gitsync / "merge.py").write_text(source + f"\ndef new_helper():\n{body}\n")
    injected = _unexpected(tmp_path, gitsync)
    assert any(found_rule == rule for _rel, found_rule, _anchor in injected), injected
