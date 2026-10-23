#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The launch-fault halt of an issue run (belief 3, belief 8, hard line 4).
#
# A launch fault is a run that fails before any stage starts: glide exits with
# rc=2 (a configuration error, such as a refused `pre_run` hook, or invalid
# pipeline inputs), or the issue branch cannot be cut. It is a fault of this host, not of the issue, so the next
# issue would fail the same way. The run keeps its claim and halts the line with
# the D65 pattern: the `$HALT` marker plus `halt_record_write`. `_drive` reads
# the marker before selection, so no tick claims another issue.
#
# The retry is the re-check. `launchfault_due` removes the marker once the
# back-off has passed, and `_drive_resume` relaunches the same issue from the
# record. A run that starts clears the fault state; a run that fails at launch
# again halts again. After the third failed attempt one Needs You goes out, and
# the retries go on quietly at the last interval. No hand step resumes the line.
#
# The state file holds one line: `<issue> <attempts> <next retry epoch> <notified>`.

LAUNCHFAULT_FILE_NAME=launch-fault
LAUNCHFAULT_PREFIX="launch fault:"

launchfault_now() { date +%s; }

# A failed issue run is a launch fault when glide stopped it before any stage.
launchfault_is() {   # <rc>
  [ "${work_kind:-phase}" = issue ] && [ "$1" = 2 ]
}

# Halt the line on a launch fault, keep the claim, and plan the next attempt.
launchfault_halt() {   # <issue> <task file> <run dir> <state> <rc> <session log> <offset> [reason]
  local number="$1" rel="$2" run_dir="$3" state="$4" rc="$5"
  local file="$RUN_DIR/$LAUNCHFAULT_FILE_NAME"
  local seen="" attempts=0 _next="" notified=0 delay=1200 reason
  [ -f "$file" ] && read -r seen attempts _next notified <"$file" || true
  if [ "$seen" != "$number" ]; then attempts=0; notified=0; fi
  attempts=$((attempts + 1))
  [ "$attempts" -ge 2 ] || delay=600
  halt_state_from_session "$6" "$7"
  [ -z "$HALT_LOG_STATE" ] || state="$HALT_LOG_STATE"
  reason="${8:-${HALT_LOG_REASON:-glide exited with rc=$rc before any stage ran}}"
  printf '%s issue %s attempt %s rc=%s state=%s at %s\n' "$LAUNCHFAULT_PREFIX" \
    "$number" "$attempts" "$rc" "${state:-unknown}" "$(ts)" >"$HALT"
  halt_record_write "$number" "$rel" "$run_dir" "$state" "$rc" launch-fault "$reason"
  if [ "$attempts" -ge 3 ] && [ "$notified" != 1 ]; then
    notify "Needs You. Issue $number cannot start, and no other issue is taken: $reason
Action: fix that cause on this host.
Check: the next retry starts the run. Argus retries issue $number every $((delay / 60)) minutes and resumes alone."
    notified=1
  fi
  printf '%s %s %s %s\n' "$number" "$attempts" "$(($(launchfault_now) + delay))" \
    "$notified" >"$file"
  log "launch-fault: issue $number attempt $attempts rc=$rc state=${state:-unknown}; claim kept, no new issue; retry in $((delay / 60)) min"
  tmux_set_title "HALTED issue $number (launch fault)"
}

# The run started, so the launch fault is gone.
launchfault_clear() {
  rm -f "$RUN_DIR/$LAUNCHFAULT_FILE_NAME"
}

# On a tick, lift a launch-fault halt whose back-off has passed, so the tick
# resumes the same issue from the halt record. Any other halt is left alone.
launchfault_due() {
  local file="$RUN_DIR/$LAUNCHFAULT_FILE_NAME" number attempts next _notified
  [ -f "$HALT" ] && [ -f "$file" ] || return 0
  case "$(cat "$HALT")" in
    "$LAUNCHFAULT_PREFIX"*) ;;
    *) return 0 ;;
  esac
  read -r number attempts next _notified <"$file" || return 0
  case "$next" in ''|*[!0-9]*) return 0 ;; esac
  [ "$(launchfault_now)" -ge "$next" ] || return 0
  rm -f "$HALT"
  log "launch-fault: retrying issue $number after attempt $attempts"
}

# The cause of a refused issue branch cut, in the words of the #215 notice.
launchfault_branch_reason() {   # <gitsync error>
  printf 'Argus could not put it on its own branch, so nothing ran on the base branch. Cause: %s' \
    "${1:-see phase-branch.json in the driver run folder}"
}
