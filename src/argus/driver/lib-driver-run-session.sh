#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Run session: the tmux session loop, its stall watch, and the heartbeat.
# Sourced by lib-driver-run.sh; not executable on its own.
#

run_session() {
  # Every caller must hold a verified claim before this function runs. The
  # production entry is the `--session` gate: phase-driver.sh runs
  # `session_claim_ensure` first and exits on a refusal, and the tick's pane
  # carries the fence it verified. A direct call with no claim runs phase work
  # the board does not cover, so any new caller has to pass through the same
  # gate first. The pre-launch renew below is the last check, not the gate.
  local number="$1" rel="$2" rc=0 state started_at estimate_run="" halt_reason=""
  local state_source="rc"
  # Set only when the real glide job fails after its retries; see the rc case.
  local job_failed=0
  started_at="$(date +%s)"
  CUR_PHASE="$number"
  CUR_PHASE_FILE="$rel"
  mkdir -p "$RUN_DIR"
  # The session log is opened for append and grows across launches. Record its
  # length here, before glide can write anything, so a halt reads only what this
  # launch added and can never adopt an earlier run's payload. The size is read
  # through Python because `stat` and `wc` are not commands the installed-command
  # test provides. A log that does not exist yet cannot hold an earlier payload,
  # so its length is genuinely 0. Any other failed read leaves the offset EMPTY,
  # which is not zero: zero is a valid offset meaning "read the whole file", so
  # it would adopt an earlier launch's payload. An empty offset proves nothing
  # and the halt answers `unknown` from `rc`, exactly as it does for an unset or
  # non-numeric offset.
  local session_log="$RUN_DIR/session-$number.log" session_log_offset=""
  session_log_offset="$("$PYTHON" -c 'import os,sys
try:
    print(os.path.getsize(sys.argv[1]))
except FileNotFoundError:
    print(0)
except OSError:
    raise SystemExit(1)' "$session_log" 2>/dev/null || true)"
  case "$session_log_offset" in
    ''|*[!0-9]*) session_log_offset="" ;;
  esac
  trap 'on_stop 129' HUP
  trap 'on_stop 130' INT
  trap 'on_stop 143' TERM
  printf '%s\n' "$$" >"$PIDFILE"
  cd "$REPO"
  # The claim is held as `claimed` until the work starts, so a session that never
  # launched leaves a claim the queue can take back. Mark it running here, with
  # the fence the tick or the session gate put in the environment, and stop on a
  # refusal: a failed renew means the board no longer covers this run, and work
  # that starts anyway is the one path that runs a phase without a live claim.
  # The helper is only present when the driver's own files are sourced; a harness
  # that calls run_session alone has no board fence to mark.
  if [ -n "${CLIO_RESERVATION_ID:-}" ] \
      && declare -F session_claim_start_running >/dev/null; then
    session_claim_start_running "$number" || return 1
  fi
  tmux_set_title "${work_kind:-phase} $number running"
  notify_phase "$rel" started
  if [ "${CLIO_RESUME:-}" = 1 ]; then
    # The parent read the resume gate before it launched, and the state can
    # still move in between: another machine, or an operator, may have taken the
    # phase on. Re-check it and name it, then let glide run the two-tick repair.
    # A moved state is evidence to repair from, not a reason to write the halt
    # marker from the child.
    local resume_run_dir resume_state resume_reason
    resume_run_dir="$(phase_run_dir "$number" "$rel")"
    resume_state="$(phase_state "$number" "$rel")"
    resume_reason="$(resume_gate_reason "$resume_state" "$resume_run_dir")" || \
      log "resume gate: phase $number: $resume_reason; continuing for the repair"
  fi
  if [ -n "${DRIVER_STUB_RC:-}" ]; then
    log "STUB mode: simulating glide (rc=$DRIVER_STUB_RC)"
    # A stub can be told to wait for a file instead of sleeping a fixed span. A
    # fixed sleep is a duration the test guesses: on a loaded host the guess is
    # over before the condition under test is set up, and a check that needs the
    # stub to still be running then fails because the stub finished. Waiting for a
    # file the test creates at the moment it needs the stub alive removes the
    # guess. The timeout bounds the wait so a forgotten release cannot hang the
    # self-test.
    if [ -n "${DRIVER_STUB_WAIT_FILE:-}" ]; then
      # HUP is ignored while the stub waits, because the orphan case depends on
      # the pane process outliving the kill of its window. Left alone, the trap
      # would fire on the kill, halt the line, and remove the recorded PID — the
      # driver would then correctly find nothing to orphan and the case would
      # never be reached. Ignoring it here is what makes this a test of the
      # orphan branch rather than of the signal branch.
      trap '' HUP
      # Announced before waiting, not after: the recorded PID appears the moment
      # the pane starts, which is before the stub is in a state to be orphaned. A
      # test that acted on the PID alone could kill the window while the trap was
      # still the one that halts, and would then be testing the signal branch
      # while believing it tested the orphan branch.
      [ -z "${DRIVER_STUB_READY_FILE:-}" ] || : >"$DRIVER_STUB_READY_FILE"
      local waited=0
      while [ ! -e "$DRIVER_STUB_WAIT_FILE" ] && [ "$waited" -lt 600 ]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      [ -e "$DRIVER_STUB_WAIT_FILE" ] \
        || log "STUB mode: timed out after $((waited * 100))ms waiting for $DRIVER_STUB_WAIT_FILE"
    else
      sleep "${DRIVER_STUB_SLEEP:-2}"
    fi
    rc="$DRIVER_STUB_RC"
  else
    local glide_inputs=() input input_values
    if input_values="$(glide_input_args "$number" "$rel")"; then
      while IFS= read -r input; do glide_inputs+=("$input"); done <<<"$input_values"
      # glide refuses to rerun a terminal run unless --fresh archives it first;
      # glide_run_once recomputes that per attempt. A `blocked` run is resumable
      # and is left alone, and a missing run.json means there is nothing to archive.
      local prior_state
      prior_state="$(terminal_state_of_file "$(phase_run_dir "$number" "$rel")/run.json")"
      # A run that continues an earlier one measures only part of the phase, so
      # the estimate factor skips it (estimate_run=1 marks it resumed).
      estimate_run=0
      if [ "${CLIO_RESUME:-}" = 1 ] \
        || { [ -z "$prior_state" ] && [ -f "$(phase_run_dir "$number" "$rel")/run.json" ]; }; then
        estimate_run=1
      fi
      if [ "${CLIO_RESUME:-}" = 1 ]; then
        # The parent proved the ledger against HEAD and asked glide to continue
        # the run it stopped. `--fresh` archives exactly the state the resume
        # exists to keep, so it is never added here.
        log "${work_kind:-phase} $number is resuming (prior state ${prior_state:-none}); the run is not archived"
      fi
      # Retry the job before giving up on it; glide_run_once and job_retry do the work.
      job_retry glide_run_once || { rc=$?; job_failed=1; }
    else
      log "invalid pipeline inputs; glide was not launched"
      rc=2
    fi
  fi
  state="$(phase_state "$number" "$rel")"
  local launch_fault=0; launchfault_is "$rc" "$session_log" "$session_log_offset" && launch_fault=1 || launchfault_clear
  local run_dir result_file result_state
  run_dir="$(phase_run_dir "$number" "$rel")"
  result_file="$run_dir/result.json"
  # `repair_pending` in `result.json` is a no-launch, resumable tick that must
  # not archive or finalize the run. The rc=2 halt keeps its own path.
  if [ "$rc" != 2 ] && [ -f "$result_file" ]; then
    result_state="$("$PYTHON" -c 'import json,sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    data = {}
print(data.get("state", "") if isinstance(data, dict) else "")' \
      "$result_file" 2>/dev/null || true)"
    if [ "$result_state" = repair_pending ]; then
      local repair_reason
      repair_reason="$("$PYTHON" -c 'import json,sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
step = str(data.get("step", ""))
reason = str(data.get("reason", ""))
print(f"{step}: {reason}" if step and reason else (reason or step))' \
        "$result_file" 2>/dev/null || true)"
      log "repair-pending: ${work_kind:-phase} $number${repair_reason:+: $repair_reason}; no launch, the next tick retries"
      tmux_set_title "${work_kind:-phase} $number repair pending"
      rm -f "$CURRENT" "$PIDFILE"
      exit 0
    fi
  fi
  if [ "${work_kind:-phase}" = issue ] && [ "$state" = writeback_pending ]; then
    gitsync_clean "$number"
    tmux_set_title "issue $number report pending"
    log "issue $number report is pending; claim kept for the next tick"
    rm -f "$CURRENT" "$PIDFILE"
    exit 0
  fi
  if [ "$state" = "remedy_escalated" ]; then
    # The remedy approver used every round and findings are still open. That is
    # a budget fact, not a verdict: record the residue, release the claim, and
    # continue with the next phase. No halt marker is written, and the selector
    # skips a remedy_escalated phase, so the line neither stops nor loops on it.
    handle_escalation "$number" "$rel"
    rm -f "$CURRENT" "$PIDFILE"
    exit 0
  fi
  case "$rc" in
    0)   # Land the closing run-state writes before the next phase's publish
         # has to look at them; see gitsync_clean.
         gitsync_clean "$number"
         [ "${work_kind:-phase}" != phase ] || [ -z "$estimate_run" ] \
           || estimate_update "$number" "$rel" "$started_at" "$estimate_run"
         tmux_set_title "${work_kind:-phase} $number done"
         notify_phase "$rel" completed ;;
    3)   : ;;
    130) tmux_set_title "HALTED ${work_kind:-phase} $number (interrupted)"
         notify_phase "$rel" interrupted "the process received an interrupt signal" ;;
    *)   # An issue launch fault halts the line and keeps the claim.
         if [ "$launch_fault" = 1 ]; then
           launchfault_halt "$number" "$rel" "$run_dir" "$state" "$rc" \
             "$session_log" "$session_log_offset"
           rm -f "$CURRENT" "$PIDFILE"; exit 0
         fi
         if [ "$job_failed" = 1 ] || [ "${work_kind:-phase}" = issue ]; then
           # Record the failure and let the line continue. A phase keeps its
           # claim; an issue gives its claim back.
           failure_residue_write "$number" "$rel" "$state" "$rc" "$(job_retries)"
           release_failed_issue "$number"
           log "job-failed: ${work_kind:-phase} $number rc=$rc state=${state:-unknown}; retries exhausted, recorded as failed; continuing"
           tmux_set_title "${work_kind:-phase} $number failed"
           notify_phase "$rel" failed "" "$rc"
           rm -f "$CURRENT" "$PIDFILE"
           exit 0
         fi
          # glide can exit before writing run.json. A damaged run.json is not a
          # missing one, so `$run_dir` (set above) is tested here by name.
          if [ "$rc" = 2 ]; then
            # D65: invalid pipeline inputs. This is a start-time configuration
            # halt, and it names the fix. Only an absent run.json may take its
            # state from the session log.
            if [ ! -f "$run_dir/run.json" ]; then
              halt_state_from_session "$session_log" "$session_log_offset"
              if [ -n "$HALT_LOG_STATE" ]; then
                state="$HALT_LOG_STATE"
                halt_reason="$HALT_LOG_REASON"
                state_source="session-log"
              fi
            fi
            printf '%s %s rc=%s state=%s at %s\n' \
              "${work_kind:-phase}" "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
            halt_record_write "$number" "$rel" "$run_dir" \
              "$state" "$rc" "$state_source" "$halt_reason"
            log "halt-start: phase $number rc=$rc state=${state:-unknown} source=$state_source; the pipeline inputs are invalid; fix commands.glide, pipeline, or the phase declaration, then clear the halt"
            tmux_set_title "HALTED ${work_kind:-phase} $number (rc=$rc)"
            notify_phase "$rel" stopped "" "$rc"
          else
            # D16a: glide exited non-zero with no run result and no recorded job
            # failure. The two-tick repair owns the recovery, so this path
            # writes no halt marker and no halt record; the next tick retries.
            log "repair-pending: ${work_kind:-phase} $number rc=$rc state=${state:-unknown}; no run result, the next tick retries"
            tmux_set_title "${work_kind:-phase} $number repair pending"
          fi ;;
  esac
  rm -f "$CURRENT" "$PIDFILE"
  exit "$rc"
}

# Busy means "a phase window is running", not "the session exists". The session
# is persistent and always exists once created, so testing it would block the
# line forever. A dead-but-retained phase window is reclaimed, never the
# session, so a display attached to the idle window is not disturbed.
tmux_busy() {
  tmux_phase_window_alive && return 0
  if tmux_window_exists; then
    log "stale dead phase window in '$SESSION'; reclaiming it"
    tmux_phase_window_close
  fi
  return 1
}

# While a session is live, alert once if its output has gone quiet too long.
#
# The clock deliberately does NOT watch the session log. The engine prints its
# recovery line there every 5 minutes for as long as a step is quiet, so a session
# log can never look stale while a step is stuck: the watchdog would be reset by
# the very message that means "still stuck". The phase directory is the signal
# instead — run logs, the console capture, and the `activity` marker the engine
# touches whenever the harness produces output. `reminders.log` is excluded
# because the reminder audit is engine chatter too, and it fires a few times per
# step at most.
check_stall() {
  [ -f "$STALL" ] && return 0
  local n phase_dir newest now age phase_file issue_file
  n="$(cat "$CURRENT" 2>/dev/null || true)"
  [ -n "$n" ] || return 0
  if [ "${work_kind:-phase}" = issue ]; then
    # An issue run's input is its frozen task file in issues.task-dir.
    issue_file="$(issue_task_file_of "$n")"
    if [ -z "$issue_file" ]; then
      log "issue $n has no task file; the pipeline input ${PHASE_FILE_INPUT:-phase_file} is missing, so the stall check is skipped"
      return 0
    fi
    phase_dir="$(phase_run_dir "$n" "$issue_file")"
  else
    phase_dir="$(phase_run_dir "$n")"
  fi
  newest="$(phase_activity_mtime "$phase_dir")"
  [ "$newest" -gt 0 ] || return 0
  now="$(date +%s)"; age=$((now - newest))
  if [ "$age" -gt "$STALE_AFTER_SEC" ]; then
    printf 'stalled: %s %s no agent output for %ss at %s\n' \
      "${work_kind:-phase}" "$n" "$age" "$(ts)" >"$STALL"
    if [ "${work_kind:-phase}" = issue ]; then
      notify "Issue $n looks stalled. There has been no agent output for $STALE_AFTER_SEC seconds. Inspect the run at $phase_dir."
      return 0
    fi
    phase_file="$("$PYTHON" - "$ARGUS" "$REPO" "$n" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.conventions import resolve_phase_file
print(resolve_phase_file(sys.argv[2], int(sys.argv[3])) or "")
PY
    )"
    if [ -n "$phase_file" ]; then
      notify_phase "$phase_file" stalled "" "" "$((STALE_AFTER_SEC / 60))"
    else
      notify "Phase $n looks stalled.

There has been no agent output for $STALE_AFTER_SEC seconds. The session is still running; inspect it before stopping the work."
    fi
  fi
}

# The newest mtime in the phase directory, ignoring the engine's own sidecars.
phase_activity_mtime() {
  local dir="$1" file newest
  [ -d "$dir" ] || { printf '0'; return 0; }
  newest=0
  for file in "$dir"/*; do
    [ -f "$file" ] || continue
    case "$(basename "$file")" in
      reminders.log) continue ;;
    esac
    stat_epoch "$file"
    printf '\n'
  done | sort -rn | head -1 | { read -r top || top=''; printf '%s' "${top:-0}"; }
}

prune() {
  [ "$DRY_RUN" = 1 ] && return 0
  find "$RUN_DIR" -maxdepth 1 -name 'session-*.log' -mtime "+$LOG_KEEP_DAYS" \
    -delete 2>/dev/null || true
}

heartbeat() {
  local url="${HEARTBEAT_URL:-}"
  [ -n "$url" ] || return 0
  curl -fsS -m 10 --retry 3 "$url" >/dev/null 2>&1 \
    || log "heartbeat ping failed ($url)"
}
