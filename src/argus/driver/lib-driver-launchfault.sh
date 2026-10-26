#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The launch-fault halt of an issue run (belief 3, belief 8, hard line 4).
#
# A launch fault is a run that fails before any stage starts, shown by evidence:
# glide's session record says `config_error` at hook point `pre_run`, or the
# issue branch cannot be cut. Glide also exits rc=2 for faults inside a stage,
# so rc=2 alone is not a launch fault. It is a fault of this host, not of the issue, so the next
# issue would fail the same way. The run keeps its claim and halts the line with
# the D65 pattern: the `$HALT` marker plus `halt_record_write`. `_drive` reads
# the marker before selection, so no tick claims another issue.
#
# The retry is the re-check. While halted, `launchfault_due` renews the claim
# with `claim_renew_now`, and removes the marker once the back-off has passed, and `_drive_resume` relaunches the same issue from the
# record. A run that starts clears the fault state; a run that fails at launch
# again halts again. After the third failed attempt one Needs You goes out, and
# the retries go on quietly at the last interval. No hand step resumes the line.
#
# The state file holds one line: `<issue> <attempts> <next retry epoch> <notified>`.

LAUNCHFAULT_FILE_NAME=launch-fault
LAUNCHFAULT_PREFIX="launch fault:"

launchfault_now() { date +%s; }

# A failed issue run is a launch fault only when glide's own record shows that
# its `pre_run` hook refused the run, so no stage started.
launchfault_is() {   # <rc> <session log> <offset>
  [ "${work_kind:-phase}" = issue ] && [ "$1" = 2 ] && halt_state_from_session "$2" "$3" \
    && [ "$HALT_LOG_STATE" = config_error ] && [ "$HALT_LOG_HOOK" = pre_run ]
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
  if ! issue_stop_save "$number"; then
    state="$STOP_SAVE_STATE"; reason="$STOP_SAVE_REASON"
  fi
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

# On a tick, renew the held claim, and lift a launch-fault halt whose back-off
# has passed, so the tick resumes the same issue from the halt record. A claim
# given back by any path (release, abandon) ends the fault: its halt, record and
# count go, so a later fault counts and notifies again. Any other halt is left
# alone.
launchfault_due() {
  local file="$RUN_DIR/$LAUNCHFAULT_FILE_NAME" number attempts next _notified
  [ -f "$HALT" ] && [ -f "$file" ] || return 0
  case "$(cat "$HALT")" in
    "$LAUNCHFAULT_PREFIX"*) ;;
    *) return 0 ;;
  esac
  read -r number attempts next _notified <"$file" || return 0
  if ! claim_read_fields "$CLAIM" || [ "$CLAIM_NUMBER" != "$number" ]; then
    rm -f "$file" "$HALT"
    if halt_record_read "$HALT_RECORD" && [ "$HALT_REC_PHASE" = "$number" ]; then
      rm -f "$HALT_RECORD"
    fi
    log "launch-fault: issue $number no longer holds a claim; its halt and record are dropped"
    return 0
  fi
  claim_renew_now
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

# The state and the cause of a failed issue branch cut, as `<state><TAB><reason>`.
# A refusal such as a dirty checkout carries no `state` key, so a blocked repo
# reads as BLOCKED with the cut's own summary, and a cut that wrote no result
# says so. The halt then names what was seen, not `unknown` (belief 4).
launchfault_branch_fields() {   # <phase-branch.json>
  "$PYTHON" -c 'import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        out = json.load(handle)
except (OSError, ValueError):
    out = None
if not isinstance(out, dict):
    print("NO_RESULT\tthe branch cut wrote no result")
    raise SystemExit
repos = [r for r in out.get("repos") or [] if isinstance(r, dict)]
state = out.get("state") or ("BLOCKED" if any(not r.get("ok") for r in repos) else "")
reason = " ".join(str(out.get("error") or out.get("summary") or "").split())
print(state + "\t" + reason)
' "$1"
}

# Save an issue run's edits before a stop path exits, so the next branch cut
# finds a clean checkout and recovery needs no hand edit (#288). The checkpoint
# commits and pushes them on the run's own issue branch only; another branch, or
# a claim this machine does not hold, is left alone. A failure halts the line
# with STOP_SAVE_STATE and STOP_SAVE_REASON and returns 1.
issue_stop_save() {   # <issue>
  local out="$RUN_DIR/stop-save-$1.json" fields
  STOP_SAVE_STATE="" STOP_SAVE_REASON=""
  [ "${work_kind:-phase}" = issue ] || return 0
  case "$(${CMD_GIT:-git} -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)" in
    "issue/$1-"*) ;;
    *) return 0 ;;
  esac
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --kind issue --mode checkpoint --phase "$1" >"$out" 2>/dev/null && return 0
  fields="$(launchfault_branch_fields "$out")"
  STOP_SAVE_STATE="${fields%%$'\t'*}"
  STOP_SAVE_REASON="the run's edits are not saved on its branch: ${fields#*$'\t'}"
  printf 'stop-save: issue %s state=%s at %s\n' "$1" "$STOP_SAVE_STATE" "$(ts)" >"$HALT"
  log "stop-save: issue $1 state=$STOP_SAVE_STATE; $STOP_SAVE_REASON (see $out)"
  return 1
}
