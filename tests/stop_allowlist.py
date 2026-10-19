# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver and gitsync stop allowlist: file, rule, owner function, stop line.

Regenerate by importing the test module's scanner and writing its rows; keep one
row per stop. The owner function is part of the key so a new stop in an existing
file cannot reuse another function's row.
"""

STOP_ALLOWLIST = r"""
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
