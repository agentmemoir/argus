# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The driver and gitsync stop allowlist: file, rule, owner function, stop line.

Regenerate by importing the test module's scanner and writing its rows; keep one
row per stop. The owner function is part of the key so a new stop in an existing
file cannot reuse another function's row.
"""

STOP_ALLOWLIST = r"""
    driver/lib-driver-abandon.sh	return_nonzero	fn:abandon_board_reason|case "${DRIVER_STUB_BOARD_FENCE:-}" in fail) return 1 ;; esac#1
    driver/lib-driver-abandon.sh	return_nonzero	fn:abandon_board_reason|2>"$RUN_DIR/abandon-scan-$1.err")" || return 1#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 2#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 2 ;;#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 2 ;;#2
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|driver_lock_take || { printf 'argus abandon: another driver instance holds the lock; try again\n' >&2; exit 1; }#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|driver_orphan_check || { printf 'argus abandon: a phase run is alive with no window (orphaned); not abandoning\n' >&2; exit 1; }#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#2
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#3
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#4
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1 ;;#1
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1 ;;#2
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1 ;;#3
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#5
    driver/lib-driver-abandon.sh	exit_nonzero	fn:abandon_cmd|exit 1#6
    driver/lib-driver-check.sh	return_nonzero	fn:check|return 1#1
    driver/lib-driver-check.sh	return_nonzero	fn:check|return 1#2
    driver/lib-driver-config.sh	return_nonzero	fn:phases_dir_is_local|*) return 1 ;;#1
    driver/lib-driver-config.sh	return_nonzero	fn:resolve_python|[ -n "$candidate" ] && [ -x "$candidate" ] || return 1#1
    driver/lib-driver-config.sh	return_nonzero	fn:load_config|if ! out="$(run_config "$@")"; then return 2; fi#1
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#1
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#2
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#3
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#4
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#5
    driver/lib-driver-config.sh	return_nonzero	fn:work_preflight|return 1#6
    driver/lib-driver-config.sh	exit_nonzero	fn:argus_init|*) printf 'argus: unknown driver %s; known drivers: phase, issues\n' "$DRIVER" >&2; exit 2 ;;#1
    driver/lib-driver-config.sh	exit_nonzero	fn:argus_init|exit 3#1
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_lock_take|flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }#1
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_lock_wait|return 1#1
    driver/lib-driver-drive.sh	halt_marker	fn:driver_orphan_check|[ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"#1
    driver/lib-driver-drive.sh	return_nonzero	fn:driver_orphan_check|return 1#1
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }#1
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|reconcile_pending_reviews || return 1#1
    driver/lib-driver-drive.sh	return_nonzero	fn:_drive|return 1#1
    driver/lib-driver-drive.sh	drive_rc_nonzero	fn:_drive_cut_branch|tidy_published_checkout "$number" || { _DRIVE_RC=1; return 0; }#1
    driver/lib-driver-drive.sh	drive_rc_nonzero	fn:_drive_cut_branch|_DRIVE_RC=1#1
    driver/lib-driver-drive.sh	drive_rc_nonzero	fn:_drive_cut_branch|_DRIVE_RC=1#2
    driver/lib-driver-control.sh	halt_marker	fn:stop|printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"#1
    driver/lib-driver-control.sh	return_nonzero	fn:release|return 1#1
    driver/lib-driver-control.sh	return_nonzero	fn:release|return 1#2
    driver/lib-driver-control.sh	return_nonzero	fn:release|return 1#3
    driver/lib-driver-control.sh	return_nonzero	fn:release|return 1#4
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	fn:_drive_dry_run|{ _DRIVE_RC=0; return 0; }#1
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	fn:_drive_dry_run|{ _DRIVE_RC=0; return 0; }#2
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	fn:_drive_dry_run|{ _DRIVE_RC=0; return 0; }#3
    driver/lib-driver-dryrun.sh	drive_rc_zero_refusal	fn:_drive_dry_run|{ _DRIVE_RC=0; return 0; }#4
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_renew|reserve|fail) return 1 ;;#1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_renew|return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_reserve|return 1 ;;#1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_reserve|return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:resume_fence_reserve|return 1#2
    driver/lib-driver-fence.sh	return_nonzero	fn:board_assert_owner_bounded|return 124#1
    driver/lib-driver-fence.sh	return_nonzero	fn:board_assert_owner|[ -n "$reservation_id" ] && [ -n "$generation" ] || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:board_assert_owner|[[ $phase =~ ^[0-9]+$ ]] || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:board_assert_owner|fail) return 1 ;;#1
    driver/lib-driver-group.sh	return_nonzero	fn:group_take_declared|return 1#1
    driver/lib-driver-halt-record.sh	return_nonzero	fn:halt_record_read|)" || return 1#1
    driver/lib-driver-halt-record.sh	resume_refuse	fn:resume_refuse|resume_refuse() { # <phase> <reason>#1
    driver/lib-driver-halt-record.sh	halt_marker	fn:resume_refuse|printf 'resume refused: %s\n' "$reason" >"$HALT"#1
    driver/lib-driver-halt-record.sh	return_nonzero	fn:resume_gate_reason|return 1#1
    driver/lib-driver-halt-record.sh	return_nonzero	fn:resume_gate_reason|return 1#2
    driver/lib-driver-halt-record.sh	return_nonzero	fn:resume_gate_reason|return 1#3
    driver/lib-driver-issues.sh	return_nonzero	fn:issue_preflight|return 1#1
    driver/lib-driver-issues.sh	return_nonzero	fn:issue_preflight|return 1#2
    driver/lib-driver-issues.sh	return_nonzero	fn:issue_preflight|return 1#3
    driver/lib-driver-issues.sh	return_nonzero	fn:issue_preflight|return 1#4
    driver/lib-driver-launch.sh	drive_rc_nonzero	fn:_drive_launch||| { notify "${work_kind:-phase} $number failed to create tmux session '$SESSION'"; { _DRIVE_RC=1; return 0; }; }#1
    driver/lib-driver-launch.sh	drive_rc_nonzero	fn:_drive_launch||| { notify "${work_kind:-phase} $number failed to launch in tmux"; { _DRIVE_RC=1; return 0; }; }#1
    driver/lib-driver-launchfault.sh	halt_marker	fn:launchfault_halt|"$number" "$attempts" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"#1
    driver/lib-driver-log.sh	return_nonzero	fn:cap_last_byte|printf -v byte '%d' "'$last" || return 1#1
    driver/lib-driver-maintenance.sh	return_nonzero	fn:reconcile_pending_reviews|tidy_published_checkout || return 1#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume_group|return 1#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume_branch|return 1 ;;#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume_branch|return 1#1
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "no phase file found for phase $number"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|[ "$RESUME_LAUNCH_FAULT" = 0 ] || return 1#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#1
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "the halt record $HALT_RECORD is missing or malformed"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#2
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$PREFLIGHT_REASON"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#3
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$RESUME_REFUSAL"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#4
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$RESUME_REFUSAL"#2
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#5
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "the claim fence was refused for phase $number; no launch (see the driver log)"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#6
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "the launch failed; see $RUN_DIR/driver.log"#1
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#7
    driver/lib-driver-resume.sh	resume_refuse	fn:_drive_resume|resume_refuse "$target" "$RESUME_REFUSAL"#3
    driver/lib-driver-resume.sh	return_nonzero	fn:_drive_resume|return 1#8
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 2 ;;#1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|driver_lock_take || { printf 'argus: another driver instance holds the lock; try again\n' >&2; exit 1; }#1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 1#1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|driver_orphan_check || { printf 'argus: a phase run is alive with no window (orphaned); the line is halted\n' >&2; exit 1; }#1
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 1#2
    driver/lib-driver-resume.sh	exit_nonzero	fn:resume_cmd|exit 1#3
    driver/lib-driver-run.sh	return_nonzero	fn:record_claim|return 1#1
    driver/lib-driver-run.sh	return_nonzero	fn:record_claim|return 1#2
    driver/lib-driver-run.sh	return_nonzero	fn:claim_read_fields|[ -s "$1" ] || return 1#1
    driver/lib-driver-run.sh	return_nonzero	fn:claim_read_fields|IFS= read -r line <"$1" || return 1#1
    driver/lib-driver-run.sh	halt_marker	fn:on_stop|"${work_kind:-phase}" "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"#1
    driver/lib-driver-run.sh	return_nonzero	fn:run_session|session_claim_start_running "$number" || return 1#1
    driver/lib-driver-run.sh	halt_marker	fn:run_session|"${work_kind:-phase}" "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"#1
    driver/lib-driver-run.sh	return_nonzero	fn:tmux_busy|return 1#1
    driver/lib-driver-select-flow.sh	return_nonzero	fn:_drive_choose_selection|return 1#1
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#1
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	fn:_drive_choose_selection|{ _DRIVE_RC=0; return 0; }#1
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#2
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#3
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#4
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#5
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	fn:_drive_choose_selection|{ _DRIVE_RC=0; return 0; }#2
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	fn:_drive_choose_selection|{ _DRIVE_RC=0; return 0; }#3
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#6
    driver/lib-driver-select-flow.sh	drive_rc_zero_refusal	fn:_drive_choose_selection|{ _DRIVE_RC=0; return 0; }#4
    driver/lib-driver-select-flow.sh	drive_rc_nonzero	fn:_drive_choose_selection|{ _DRIVE_RC=1; return 0; }#7
    driver/lib-driver-select-real.sh	drive_rc_nonzero	fn:_drive_real_selection|{ _DRIVE_RC=1; return 0; }#1
    driver/lib-driver-select.sh	return_nonzero	fn:tidy_published_checkout|return 1#1
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#1
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#2
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#3
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#4
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#5
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#6
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#7
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_ensure|return 1#8
    driver/lib-driver-session-claim.sh	return_nonzero	fn:session_claim_start_running|return 1#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -ge 2 ] && [ -n "$2" ] || { printf 'argus: %s needs a value\n' "$1" >&2; exit 2; }#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|exit 3#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -eq 3 ] || { echo "usage: phase-driver.sh --session <N> <phase-file>" >&2; exit 2; }#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|session_claim_ensure "$2" "$3" || exit 1#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -le 1 ] || { echo "usage: phase-driver.sh --resume [NUMBER]" >&2; exit 2; }#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -ge 2 ] || { echo "usage: phase-driver.sh abandon <NUMBER> [--reason TEXT]" >&2; exit 2; }#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|exit 2#1
    driver/phase-tmux-idle.sh	exit_nonzero	fn:<top>|exit 2#1
    driver/phase-tmux-idle.sh	exit_nonzero	fn:<top>|CONFIG_OUT="$("$PYTHON" "$PKG_DIR/config/cli.py" "$@" --format shell)" || exit 2#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_alive|wid="$(tmux_window_id)" || return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_alive|[ "$dead" = "1" ] && return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_session_ensure|return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_session_ensure||| { log "failed to create tmux session '$SESSION'"; return 1; }#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_open|"$TMUX_BIN" new-window -d -t "$SESSION" -n "$TMUX_PHASE_WINDOW" -c "$REPO" "$launch" || return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_open|wid="$(tmux_window_id)" || return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_interrupt|wid="$(tmux_window_id)" || return 1#1
    driver/phase-tmux.sh	return_nonzero	fn:tmux_phase_window_interrupt|"$TMUX_BIN" send-keys -t "$wid" C-c 2>/dev/null || return 1#1
    gitsync/branch.py	blocked_call	fn:ensure_phase_branch|return blocked(#1
    gitsync/fence.py	ok_false	fn:_refusal|"ok": False,#1
    gitsync/fence.py	raise_fenced	fn:phase_fence|raise ReservationFenced(held) from None#1
    gitsync/git.py	raise_config	fn:git|raise ConfigError("git is not on PATH") from exc#1
    gitsync/git.py	blocked_call	fn:blocked|def blocked(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:#1
    gitsync/group_body.py	ok_false	fn:update_group_pull_request|"ok": False,#1
    gitsync/group_publication.py	raise_config	fn:_declared_group|raise ConfigError(#1
    gitsync/group_publication.py	raise_config	fn:_write_publication_receipt|raise ConfigError(f"phase run directory is missing: {run_dir}")#1
    gitsync/group_publication.py	raise_config	fn:_write_publication_receipt|raise ConfigError(#1
    gitsync/group_publication.py	raise_config	fn:_write_publication_receipt|raise ConfigError(#2
    gitsync/group_publication.py	raise_config	fn:_load_phase_reservation|raise ConfigError(f"cannot read phase reservation: {exc}") from exc#1
    gitsync/group_publication.py	blocked_call	fn:_push_one|return blocked({"repo": str(repo)}, why)#1
    gitsync/group_publication.py	blocked_call	fn:_push_one|return blocked(#1
    gitsync/group_review.py	ok_false	fn:mark_group_pull_request_ready|"ok": False,#1
    gitsync/group_review.py	ok_false	fn:mark_group_pull_request_ready|"ok": False,#2
    gitsync/issue_modes.py	raise_config	fn:mode_issue_branch|raise ConfigError("branch task filename differs from the frozen issue task")#1
    gitsync/issue_modes.py	raise_config	fn:_owned_issue_branch|raise ConfigError("checked-out branch is not the claimed issue branch")#1
    gitsync/issue_modes.py	ok_false	fn:issue_mode|"ok": False,#1
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError(#1
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError("issue branches do not accept phase groups")#1
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError("--phase is required with issue publish mode")#1
    gitsync/issue_modes.py	raise_config	fn:_issue_mode|raise ConfigError(#2
    gitsync/issue_paths.py	raise_config	fn:issue_number|raise ConfigError(str(exc)) from exc#1
    gitsync/issue_paths.py	raise_config	fn:issue_branch|raise ConfigError("issue task filename does not match the issue number")#1
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError("issue publication needs a separate private checkout")#1
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError(str(exc)) from exc#1
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError(#1
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError(#2
    gitsync/issue_paths.py	raise_config	fn:private_layout|raise ConfigError("public checkout tracks private issue data")#1
    gitsync/issue_paths.py	raise_config	fn:load_issue_reservation|raise ConfigError("issue reservation file must be inside the current run")#1
    gitsync/issue_paths.py	raise_config	fn:load_issue_reservation|raise ConfigError("reservation does not belong to the current issue")#1
    gitsync/issue_paths.py	raise_config	fn:issue_scope|raise ConfigError("current issue has no valid frozen task snapshot")#1
    gitsync/issue_paths.py	raise_config	fn:issue_scope|raise ConfigError("issue data must be inside the private checkout")#1
    gitsync/main.py	raise_config	fn:main|raise ConfigError(#1
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode publish")#1
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode published")#1
    gitsync/main.py	raise_config	fn:main|raise ConfigError("--phase is required with --mode signals")#1
    gitsync/main.py	raise_config	fn:main|raise ConfigError(#2
    gitsync/main.py	ok_false	fn:main|"ok": False,#1
    gitsync/merge.py	blocked_call	fn:_merge|return blocked(#1
    gitsync/mode_clean.py	ok_false	fn:_clean_refusal|"ok": False,#1
    gitsync/mode_open_pr.py	raise_config	fn:resolve_branch|raise ConfigError(#1
    gitsync/mode_open_pr.py	raise_config	fn:resolve_branch|raise ConfigError(#2
    gitsync/mode_open_pr.py	raise_config	fn:resolve_branch|raise ConfigError(#3
    gitsync/mode_open_pr.py	ok_false	fn:_row|"ok": False,#1
    gitsync/mode_open_pr.py	raise_config	fn:mode_open_pr|raise ConfigError(missing)#1
    gitsync/mode_open_pr.py	raise_config	fn:mode_open_pr|raise ConfigError(refusal)#1
    gitsync/mode_open_pr.py	raise_config	fn:mode_open_pr|raise ConfigError(#1
    gitsync/mode_peek_ci.py	raise_config	fn:_target_from|raise ConfigError(f"--pull-request {text!r} is not a pull request number or URL")#1
    gitsync/mode_peek_ci.py	ok_false	fn:_row|"ok": False,#1
    gitsync/modes.py	ok_false	fn:mode_published|"ok": False,#1
    gitsync/modes.py	ok_false	fn:mode_published|"ok": False,#2
    gitsync/modes.py	ok_false	fn:mode_published|"ok": False,#3
    gitsync/modes.py	ok_false	fn:mode_published|"ok": False,#4
    gitsync/open_pr_base.py	raise_config	fn:resolve_base|raise ConfigError(#1
    gitsync/open_pr_ready.py	raise_config	fn:text_from_file|raise ConfigError(f"{flag} {str(source)!r} does not exist")#1
    gitsync/open_pr_ready.py	raise_config	fn:text_from_file|raise ConfigError(f"{flag} {str(source)!r} is empty")#1
    gitsync/paths.py	raise_config	fn:_phase_file_parts|raise ConfigError(#1
    gitsync/paths.py	raise_config	fn:_phase_file_parts|raise ConfigError(f"phase file carries no behaviour: {phases_name!r}")#1
    gitsync/paths.py	raise_config	fn:phase_branch_for|raise ConfigError(#1
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError(f"not a phase number: {member!r}") from exc#1
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError("a group branch needs at least one member")#1
    gitsync/paths.py	raise_config	fn:group_branch|raise ConfigError(#1
    gitsync/pr_signal_reply.py	ok_false	fn:_refused|return {"ok": False, "state": "REFUSED", "reasons": [reason], "events": []}#1
    gitsync/pr_signals.py	ok_false	fn:watch_pull_request|return {"ok": False, "state": "UNAVAILABLE", "reasons": [why], "events": []}#1
    gitsync/pr_signals.py	ok_false	fn:mode_signals|return {"ok": False, "state": "UNAVAILABLE", "reasons": [reason], "events": []}#1
    gitsync/publish.py	blocked_call	fn:_commit_phase_receipt|return blocked(#1
    gitsync/publish_issue.py	raise_config	fn:publish_issue|raise ConfigError("public checkout is not on the current issue branch")#1
    gitsync/publish_issue.py	raise_config	fn:publish_issue|raise ConfigError(why or "issue upstream names a different branch")#1
    gitsync/publish_issue.py	raise_config	fn:fence|raise ConfigError("frozen issue task does not belong to the current claim")#1
    gitsync/publish_issue.py	ok_false	fn:fence|ok=False,#1
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="RETRY", summary=synced["summary"])#1
    gitsync/publish_issue.py	ok_false	fn:fence|info, ok=False, state="RETRY", summary=summarize([clean], "publish")#1
    gitsync/publish_issue.py	ok_false	fn:fence|ok=False,#2
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="RETRY", summary=resync["summary"])#1
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="RETRY", summary=synced["summary"])#2
    gitsync/publish_issue.py	ok_false	fn:fence|ok=False,#3
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="FENCED", error=str(exc))#1
    gitsync/publish_issue.py	ok_false	fn:fence|return dict(info, ok=False, state="COORDINATION_UNAVAILABLE", error=str(exc))#1
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError(f"invalid phase number: {phase!r}") from exc#1
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError(f"phase number out of range: {phase}")#1
    gitsync/publish_phase.py	raise_config	fn:publish_phase|raise ConfigError("publication requires phase-scoped run-state commit")#1
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#1
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#2
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#3
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#4
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#5
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#6
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#7
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#8
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#9
    gitsync/publish_phase.py	ok_false	fn:publish_phase|"ok": False,#10
    gitsync/publish_phase.py	ok_false	fn:publish_phase|outcome or {"ok": False, "mode": "publish", "phase": f"{int(phase):06d}"}#1
    gitsync/runstate.py	blocked_call	fn:_commit_and_push_run_state|return blocked(#1
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(f"invalid phase number: {phase!r}") from None#1
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(f"phase number out of range: {number}")#1
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(#1
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(#2
    gitsync/runstate.py	raise_config	fn:_phase_run_pathspec|raise ConfigError(#3
    gitsync/runstate.py	blocked_call	fn:commit_leftover_run_state|blocked(#1
    gitsync/runstate.py	ok_false	fn:commit_leftover_run_state|"ok": False,#1
    gitsync/runstate.py	blocked_call	fn:commit_leftover_run_state|blocked(#2
    gitsync/runstate.py	blocked_call	fn:commit_leftover_run_state|blocked(#3
    gitsync/sync.py	blocked_call	fn:resync_integration|blocked_result = blocked(#1
    gitsync/sync.py	blocked_call	fn:sync_all|blocked({"repo": str(path), "mode": mode}, reason)#1
    gitsync/sync.py	ok_false	fn:sync_all|"ok": False,#1
    gitsync/tidy.py	blocked_call	fn:return_to_integration|return blocked(#1
    gitsync/tidy.py	blocked_call	fn:return_to_integration|return blocked(#2
    driver/lib-driver-control.sh	return_nonzero	fn:release|return 1#5
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|case "$phase" in ''|*[!0-9]*) return 1 ;; esac#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|case "$generation" in ''|*[!0-9]*) return 1 ;; esac#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|[ -n "$reservation_id" ] || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|dir="$(phase_run_dir "$phase" "" 2>/dev/null)" || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|[ -n "$dir" ] || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|mkdir -p "$dir" 2>/dev/null || return 1#1
    driver/lib-driver-fence.sh	return_nonzero	fn:record_phase_reservation|return 1#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_worktree_remove|[ -n "$tip" ] || { notify "Repair $key: Argus could not save the edits in $wt, so it kept the worktree. Copy what you need from it; the next tick tries again."; return 1; }#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_worktree_remove||| { notify "Repair $key: Argus could not save the work in $wt, so it kept the worktree. Copy what you need from it; the next tick tries again."; return 1; }#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_worktree_add||| { log "repair $key: could not fetch $branch; the next tick retries"; return 1; }#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_worktree_add||| { log "repair $key: could not add a worktree for $branch; the next tick retries"; return 1; }#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_worktree_add|return 1#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_launch|repair_worktree_add "$key" "$branch" || return 1#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_launch||| { notify "repair $key failed to create tmux session '$SESSION'"; repair_worktree_remove "$(repair_worktree "$key")"; return 1; }#1
    driver/lib-driver-repair.sh	return_nonzero	fn:repair_launch||| { notify "repair $key failed to launch in tmux"; repair_worktree_remove "$(repair_worktree "$key")"; return 1; }#1
    driver/lib-driver-repair.sh	exit_nonzero	fn:run_repair_session|trap 'rm -f "$PIDFILE"; exit 130' HUP INT TERM#1
    driver/phase-driver.sh	exit_nonzero	fn:<top>|[ "$#" -eq 4 ] || { echo "usage: phase-driver.sh --repair-session <id> <task-file> <branch>" >&2; exit 2; }#1
"""
