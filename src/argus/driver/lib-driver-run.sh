#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Run state: sessions, stall checks, escalation, and the phase run directory.
#
phase_run_dir() {
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "${2:-}" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[5:])
from argus.conventions import phase_run_dir
print(phase_run_dir(sys.argv[2], int(sys.argv[3]), sys.argv[4] or None))
PY
}

phase_state() {
  local file
  file="$(phase_run_dir "$1" "${2:-}")/run.json"
  "$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state",""))' \
    "$file" 2>/dev/null || true
}

# The state a prior run ended in when glide would refuse to rerun it without
# --fresh. glide drops a `blocked` marker and resumes; every other marker it
# refuses, including one that does not parse or carries no state, so those ask
# for a fresh start too. Only an absent marker returns empty, because there is
# nothing to archive.
terminal_state_of_file() {
  local file="$1" state
  [ -f "$file" ] || return 0
  state="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("state",""))' \
    "$file" 2>/dev/null || true)"
  case "$state" in
    blocked) return 0 ;;
    "") printf '%s' "unreadable" ;;
    *) printf '%s' "$state" ;;
  esac
}

# Record the claim fence beside the run, so a later `--release` invocation can
# release the board group. The tick writes it as soon as the selection hands it a
# claim, so a tick that refuses before the launch still leaves the group
# releasable; the launch that follows reads the same pair back.
record_claim() {
  local number="$1" reservation_id="${2:-}" generation="${3:-}"
  if [ -z "$reservation_id" ] || [ -z "$generation" ]; then
    # An empty pair proves nothing about the board: the caller may have failed
    # to read a live claim, and deleting the file would strand that fence on
    # the board until its window closes. The claim file is removed only when it
    # provably holds no releasable fence (missing, empty, or missing fields);
    # a claim with a full pair is left alone, so a live fence is never lost to
    # a transient selection failure.
    if [ -s "$CLAIM" ]; then
      claim_read_fields "$CLAIM" || true
      if [ -z "$CLAIM_ID" ] || [ -z "$CLAIM_GEN" ]; then
        rm -f "$CLAIM"
      fi
    fi
    printf 'argus: the board did not provide a full claim fence for phase %s; refusing to launch\n' \
      "$number" >&2
    return 1
  fi
  # The board is the authority: a pair that names no live reservation this
  # machine owns must not become the local fence, and a board that cannot
  # answer fails closed. The claim file is written only on a confirmed fence.
  if ! board_assert_owner "$number" "$reservation_id" "$generation"; then
    printf 'argus: the board does not confirm the claim for phase %s; the fence is refused (see %s/assert-owner-%s.json)\n' \
      "$number" "$RUN_DIR" "$number" >&2
    return 1
  fi
  # A hand-started --session writes the claim before run_session creates the
  # run directory, so the write makes the folder it needs. The tick path has
  # the folder already; mkdir -p answers both.
  mkdir -p "$RUN_DIR"
  printf '%s\t%s\t%s\n' "$number" "$reservation_id" "$generation" >"$CLAIM"
}

# Read the claim fence into CLAIM_NUMBER, CLAIM_ID and CLAIM_GEN.
#
# The tab is split by hand rather than with `IFS=$'\t' read -r a b c`. A tab is
# an IFS *whitespace* character, so an empty field between two tabs collapses and
# every field after it shifts by one: a claim written with no fence would read
# its generation as its reservation id, and a resume would renew a fence that is
# not there. Returns non-zero when there is no claim to read.
claim_read_fields() {   # <file>
  CLAIM_NUMBER=""; CLAIM_ID=""; CLAIM_GEN=""
  local line rest
  [ -s "$1" ] || return 1
  IFS= read -r line <"$1" || return 1
  CLAIM_NUMBER="${line%%$'\t'*}"
  rest="${line#*$'\t'}"
  if [ "$rest" != "$line" ]; then
    CLAIM_ID="${rest%%$'\t'*}"
    CLAIM_GEN="${rest#*$'\t'}"
  fi
  return 0
}

# The pipeline input flags for one phase, as separate words.
#
# The names come from the configuration and the pipeline declaration, not from
# this script, so a pipeline that renames its inputs still gets what it declared.
glide_input_args() {   # <number> <rel>
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "$2" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[5:])
from argus.conventions import pipeline_inputs
inputs = pipeline_inputs(sys.argv[2], int(sys.argv[3]), sys.argv[4])
for key in (conventions.setting("number-input", "phase_number"), conventions.setting("file-input", "phase_file")):
    value = inputs[key]
    print("--input")
    print(f"{key}={value if isinstance(value, str) else json.dumps(value)}")
PY
}

# A signal-driven stop must halt and notify: bash dies before any trailing
# code after a signal, so the work is done in a trap, marker first. (Ctrl-C
# reaches this; tmux kill-session does NOT signal the pane, which is why the
# driver also records the session PID and treats a live PID with no session
# as an orphan.)
#
# A release is different on purpose. The parent writes $RELEASE before it sends
# the interrupt, and that marker says this stop is intentional: the group is
# going back to the queue, not being halted. Then no halt marker is written and
# no halt notification is sent, and the release path finishes the board write.
on_stop() {
  local rc="$1"
  [ "$_STOPPED" = 1 ] && exit "$rc"
  _STOPPED=1
  if [ -f "$RELEASE" ]; then
    rm -f "$RELEASE"
  elif [ -n "$CUR_PHASE" ]; then
    printf '%s %s stopped by signal (rc=%s) at %s\n' \
      "${work_kind:-phase}" "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    notify_phase "$CUR_PHASE_FILE" stopped-signal
  fi
  rm -f "$CURRENT" "$PIDFILE"
  exit "$rc"
}

# Say what a gitsync result file reported, without changing how the driver goes on.
#
# Both clean paths answer `ok` and exit 0 for work they did not do. `degraded`
# keeps `ok` true so that no caller halts the line, and puts its reason in
# `warning`; the whole-tree sweep can skip a phase whose fence is unproven and
# still answer ok, because a sibling phase landed. The exit code cannot carry
# either fact, so the driver reads the file it just wrote and says both itself.
#
# A file that is missing, empty, unreadable, or not a JSON object logs nothing.
# The caller has already logged a non-zero exit for a run that failed, and a
# result the driver cannot parse is not a second fault worth a line.
#
# The warning is git's own stderr, which spans lines, so it is flattened and cut
# the way every other free-text reason in this driver is. The reader prints one
# tab-separated record per fact, with each field already flattened to one line,
# because the loop below reads one record per line and a raw newline in the
# warning would otherwise be read as the start of a second record.
gitsync_log_result() {
  local file="$1" label="$2" kind first second third
  [ -s "$file" ] || return 0
  "$PYTHON" -c 'import json,sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    raise SystemExit(0)
if not isinstance(data, dict):
    raise SystemExit(0)
def flat(value):
    text = "" if value is None else str(value)
    return " ".join(text.split())
warning = data.get("warning")
if isinstance(warning, str) and warning.strip():
    print("warning\t" + flat(warning))
skipped = data.get("skipped")
if isinstance(skipped, list):
    for one in skipped:
        if not isinstance(one, dict):
            continue
        print("skipped\t" + flat(one.get("phase", "")) + "\t"
              + flat(one.get("state", "")) + "\t" + flat(one.get("error", "")))' \
    "$file" 2>/dev/null | while IFS=$'\t' read -r kind first second third; do
    case "$kind" in
      warning)
        log "$label continued in a degraded state: $(cap_length "$(flatten_one_line "$first")" 500)" ;;
      skipped)
        log "$label skipped phase $first ($second): $(cap_length "$(flatten_one_line "$third")" 500)" ;;
    esac
  done
  return 0
}

# Commit and push one finished phase's closing private run state.
#
# The runner writes the ledger's finalize row, the run marker, the usage files
# and the deleted resume record *after* finalize's publish commit, so those
# writes were never carried. The next phase's publish then found them dirty and
# refused with "working tree is dirty after phase cleanup", which halted the
# line. This is the private-only, phase-scoped commit that lands them. A failure
# is logged and never halts: the next phase's publish reports it if it recurs.
gitsync_clean() {
  local number="$1" rc=0
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    --phase "$number" >"$RUN_DIR/phase-$number-run-state.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not commit ${work_kind:-phase} $number closing run state (rc=$rc; see $RUN_DIR/phase-$number-run-state.json)"
  fi
  gitsync_log_result "$RUN_DIR/phase-$number-run-state.json" \
    "${work_kind:-phase} $number closing run state"
  return 0
}

# Land every phase's leftover closing run state before this launch.
#
# gitsync_clean lands a phase's closing writes when its run ends cleanly, but a
# run that ended without one — interrupted, blocked, or finished before that
# cleanup existed — leaves the writes dirty. The next phase's publish then
# refuses with "working tree is dirty after phase cleanup" and halts the line.
# Committing every phase's leftovers here, each scoped to its own directory,
# closes that gap before the launch without carrying another phase's partial
# records into this phase's commit. A failure is logged and never halts: the
# publish that would have refused reports it instead.
gitsync_land_leftovers() {
  local rc=0
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    >"$RUN_DIR/run-state-leftovers.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not land leftover run state before launch (rc=$rc; see $RUN_DIR/run-state-leftovers.json)"
  fi
  gitsync_log_result "$RUN_DIR/run-state-leftovers.json" "leftover run state"
  return 0
}

# Record an exhausted remedy loop and continue the line.
#
# Exhausting the approver's rounds is a fact about the budget, not a verdict on
# the work, so the run ends non-terminal (`remedy_escalated`): this writes the
# open findings and where their grounds live to the run directory, releases the
# phase's claim on the board, and writes no halt marker. A failure in either
# half is logged; the run record's own state is what the selector reads, so the
# phase is skipped either way.
handle_escalation() {
  local number="$1" rel="$2" phase_dir out rc=0
  phase_dir="$WF/phase-$(printf '%06d' "$number")"
  out="$RUN_DIR/escalation-$number.json"
  local args=("${ARGUS_CONFIG_ARGS[@]}" --run-dir "$phase_dir" --phase "$number" --repo-root "$REPO")
  [ -z "${PHASE_MACHINE_ID:-}" ] || args+=(--machine-id "$PHASE_MACHINE_ID")
  [ -z "${CLIO_RESERVATION_ID:-}" ] || args+=(--reservation-id "$CLIO_RESERVATION_ID")
  [ -z "${CLIO_RESERVATION_GENERATION:-}" ] || args+=(--generation "$CLIO_RESERVATION_GENERATION")
  "$PYTHON" "$ARGUS/board/remedy.py" "${args[@]}" \
    >"$out" 2>/dev/null || { rc=$?; log "escalation record for ${work_kind:-phase} $number could not be completed (rc=$rc; see $out)"; }
  # Land the record and release the claim before saying so, so a person reading
  # the notification finds the evidence where it points.
  gitsync_clean "$number"
  tmux_set_title "${work_kind:-phase} $number escalated"
  notify_phase "$rel" escalated
  return 0
}

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
  local run_dir result_file result_state
  run_dir="$(phase_run_dir "$number" "$rel")"
  result_file="$run_dir/result.json"
  # Glide writes one atomic `result.json` for every state. Its repair tick is
  # `repair_pending`: a no-launch, resumable tick that must not archive or
  # finalize the run, even though glide exits 0 and writes no run.json. The rc=2
  # invalid-input halt keeps its own path.
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
    *)   if [ "$job_failed" = 1 ] || [ "${work_kind:-phase}" = issue ]; then
           # Record the failure so the operator can see it, keep the claim, and
           # let the line continue. The claim resumes through its own expiry
           # windows plus the cooldown.
           failure_residue_write "$number" "$rel" "$state" "$rc" "$(job_retries)"
           log "job-failed: ${work_kind:-phase} $number rc=$rc state=${state:-unknown}; retries exhausted, recorded as failed; continuing"
           tmux_set_title "${work_kind:-phase} $number failed"
           notify_phase "$rel" failed "" "$rc"
           rm -f "$CURRENT" "$PIDFILE"
           exit 0
         fi
          # glide can exit before writing run.json, as a configuration error
          # does. A run.json that exists but does not parse is a damaged
          # result, not a missing one: `phase_state` returns empty for both, so
          # the file is tested here by name.
          local run_dir
          run_dir="$(phase_run_dir "$number" "$rel")"
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
  local n phase_dir newest now age phase_file
  n="$(cat "$CURRENT" 2>/dev/null || true)"
  [ -n "$n" ] || return 0
  phase_dir="$(phase_run_dir "$n")"
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
