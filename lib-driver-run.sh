#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
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

# Record the launch fence beside the run, so a later `--release` invocation can
# release the board group. The parent that launches the run owns the token for
# the life of the run; the release mode is a separate process and reads it here.
record_claim() {
  local number="$1" reservation_id="${2:-}" generation="${3:-}"
  if [ -z "$reservation_id" ] || [ -z "$generation" ]; then
    rm -f "$CLAIM"
    return 0
  fi
  printf '%s\t%s\t%s\n' "$number" "$reservation_id" "$generation" >"$CLAIM"
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
    printf 'phase %s stopped by signal (rc=%s) at %s\n' \
      "$CUR_PHASE" "$rc" "$(ts)" >"$HALT"
    notify_phase "$CUR_PHASE_FILE" stopped-signal
  fi
  rm -f "$CURRENT" "$PIDFILE"
  exit "$rc"
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
  "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    --phase "$number" >"$RUN_DIR/phase-$number-run-state.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not commit phase $number closing run state (rc=$rc; see $RUN_DIR/phase-$number-run-state.json)"
  fi
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
  "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    >"$RUN_DIR/run-state-leftovers.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not land leftover run state before launch (rc=$rc; see $RUN_DIR/run-state-leftovers.json)"
  fi
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
  "$PYTHON" "$ARGUS/remedy_escalation.py" "${args[@]}" \
    >"$out" 2>/dev/null || { rc=$?; log "escalation record for phase $number could not be completed (rc=$rc; see $out)"; }
  # Land the record and release the claim before saying so, so a person reading
  # the notification finds the evidence where it points.
  gitsync_clean "$number"
  tmux_set_title "phase $number escalated"
  notify_phase "$rel" escalated
  return 0
}

run_session() {
  local number="$1" rel="$2" rc=0 state
  CUR_PHASE="$number"
  CUR_PHASE_FILE="$rel"
  mkdir -p "$RUN_DIR"
  trap 'on_stop 129' HUP
  trap 'on_stop 130' INT
  trap 'on_stop 143' TERM
  printf '%s\n' "$$" >"$PIDFILE"
  cd "$REPO"
  tmux_set_title "phase $number running"
  notify_phase "$rel" started
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
    if input_values="$("$PYTHON" - "$ARGUS" "$REPO" "$number" "$rel" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
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
    print(f"{key}={value if isinstance(value, str) else json.dumps(value)}")
PY
    )"; then
      while IFS= read -r input; do glide_inputs+=(--input "$input"); done <<<"$input_values"
      # glide refuses to rerun a run that already reached a terminal state unless
      # --fresh archives that run first. The selector skips the states it knows
      # need a decision, but any other terminal run it hands back (an older end
      # state, or a state added later) would wedge every tick on that refusal:
      # the driver would launch, glide would exit 2, and the driver would halt.
      # Archive and restart instead. A `blocked` run is resumable and is left
      # alone, and a missing run.json means there is nothing to archive.
      local prior_state fresh_args=()
      prior_state="$(terminal_state_of_file "$(phase_run_dir "$number" "$rel")/run.json")"
      if [ -n "$prior_state" ]; then
        fresh_args+=(--fresh)
        log "phase $number run is terminal ($prior_state); restarting it with --fresh"
      fi
      "$CMD_GLIDE" run --pipeline "$PIPELINE" \
        "${fresh_args[@]+"${fresh_args[@]}"}" "${glide_inputs[@]}" || rc=$?
    else
      log "invalid pipeline inputs; glide was not launched"
      rc=2
    fi
  fi
  state="$(phase_state "$number" "$rel")"
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
         tmux_set_title "phase $number done"
         notify_phase "$rel" completed ;;
    3)   : ;;
    130) tmux_set_title "HALTED phase $number (interrupted)"
         notify_phase "$rel" interrupted "the process received an interrupt signal" ;;
    *)   printf 'phase %s rc=%s state=%s at %s\n' \
           "$number" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
          # The title is set before the window closes, so the halt survives the
          # phase window going away and the idle display taking over.
          tmux_set_title "HALTED phase $number (rc=$rc)"
          notify_phase "$rel" stopped "" "$rc" ;;
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
    printf 'stalled: phase %s no agent output for %ss at %s\n' \
      "$n" "$age" "$(ts)" >"$STALL"
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
