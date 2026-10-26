#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Run state: the phase run directory, the claim fence, stop handling, and escalation.
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

# The frozen task file of an issue, from issues.task-dir. Empty when none exists.
issue_task_file_of() {   # <number>
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.conventions import resolve_issue_file
path = resolve_issue_file(sys.argv[2], int(sys.argv[3]))
if path is not None:
    try:
        path = path.resolve().relative_to(Path(sys.argv[2]).resolve())
    except ValueError:
        pass
print(path or "")
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

# Commit and push one finished phase's closing private run state.
#
# The runner writes the ledger's finalize row, the run marker, the usage files
# and the deleted resume record *after* finalize's publish commit, so those
# writes were never carried. The next phase's publish then found them dirty and
# refused with "working tree is dirty after phase cleanup", which halted the
# line. This is the private-only, phase-scoped commit that lands them. A failure
# is logged and never halts: the next phase's publish reports it if it recurs.
# The first line of a degraded result's warning in a gitsync JSON file, or
# nothing. A degraded step exits 0, so the exit code cannot show it did not land.
run_state_warning() {
  "$PYTHON" -c 'import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
warning = data.get("warning") if isinstance(data, dict) else None
if isinstance(warning, str) and warning.strip():
    print(warning.splitlines()[0])' "$1" 2>/dev/null || true
}

gitsync_clean() {
  local number="$1" rc=0 warning=""
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    --phase "$number" >"$RUN_DIR/phase-$number-run-state.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not commit ${work_kind:-phase} $number closing run state (rc=$rc; see $RUN_DIR/phase-$number-run-state.json)"
  else
    warning="$(run_state_warning "$RUN_DIR/phase-$number-run-state.json")"
    if [ -n "$warning" ]; then
      log "${work_kind:-phase} $number closing run state did not land: $warning"
    fi
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
  local rc=0 warning=""
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode clean \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    >"$RUN_DIR/run-state-leftovers.json" 2>/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "could not land leftover run state before launch (rc=$rc; see $RUN_DIR/run-state-leftovers.json)"
  else
    warning="$(run_state_warning "$RUN_DIR/run-state-leftovers.json")"
    if [ -n "$warning" ]; then
      log "leftover run state did not land: $warning"
    fi
  fi
  # A whole-tree sweep lands some phases and skips others and still exits 0,
  # so the exit code cannot carry a skip. Say each one; never change the rc.
  "$PYTHON" -c 'import json,sys


def clean(value):
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return " ".join(text.split())


try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = None
unreadable = not isinstance(data, dict)
if not unreadable:
    skipped = data.get("skipped")
    if data.get("state") == "PARTIAL" and (not isinstance(skipped, list) or not skipped):
        unreadable = True
    elif skipped is not None and not isinstance(skipped, list):
        unreadable = True
    elif isinstance(skipped, list):
        for index, one in enumerate(skipped):
            if not isinstance(one, dict):
                unreadable = True
                continue
            print("skip", index,
                  clean(one.get("phase", "")) or "unknown",
                  clean(one.get("state", "")) or "unknown",
                  clean(one.get("error", "")), sep="\t")
if unreadable:
    print("unreadable")' \
    "$RUN_DIR/run-state-leftovers.json" 2>/dev/null |
    while IFS=$'\t' read -r kind index phase state reason; do
      case "$kind" in
        skip) log_changed "leftovers-skipped-$index" "leftover run state skipped phase $phase ($state): $(cap_length "$(flatten_one_line "$reason")" 500)" ;;
        unreadable) log_changed "leftovers-unreadable" "could not read the clean sweep result at $RUN_DIR/run-state-leftovers.json; skipped phases are unknown" ;;
      esac
    done || true
  return 0
}

# A failed issue run gives its board claim back, so the claim label alone holds
# the issue: removing the label lets Argus retry, and work that completed,
# published, or was rejected stays retained on the board (decision 9).
# ISSUE_CLAIM_HELD=1 tells the notice that the claim is still held (belief 4).
release_failed_issue() {
  ISSUE_CLAIM_HELD=0
  [ "${work_kind:-phase}" = issue ] || return 0
  ISSUE_CLAIM_HELD=1
  if [ -z "${CLIO_RESERVATION_ID:-}" ] || [ -z "${CLIO_RESERVATION_GENERATION:-}" ]; then
    log "claim: no board token for failed issue $1; the claim waits for its expiry"
    return 0
  fi
  CLAIM_RELEASED=0
  release_refused_claim "$1" "$CLIO_RESERVATION_ID" "$CLIO_RESERVATION_GENERATION" failed
  [ "$CLAIM_RELEASED" = 1 ] && ISSUE_CLAIM_HELD=0
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
  release_failed_issue "$number"
  tmux_set_title "${work_kind:-phase} $number escalated"
  notify_phase "$rel" escalated
  return 0
}

# The session loop, its stall watch, and the heartbeat sit in their own file.
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-driver-run-session.sh"
