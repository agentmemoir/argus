#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The halt record, and the gate that decides whether it may be resumed.
#
# A halt is sticky on purpose, and the phase it stopped is still the queue's
# work. Clearing the marker alone used to advance the queue instead of resuming
# the halted phase, because the halt itself left no record to resume from. The
# record is written only at the run-end halt and at a resume refusal, so the
# operator stop, the orphan path and the signal trap keep their marker-only
# behaviour: those leave a run the board claim recovers, and a record would make
# the gate refuse it.

# Set by halt_record_read, and initialised so `set -u` holds on the paths that
# never read a record.
HALT_REC_PHASE=""
HALT_REC_PHASE_FILE=""
HALT_REC_RUN_DIR=""
HALT_REC_STATE=""
HALT_REC_RC=""
HALT_REC_RESERVATION_ID=""
HALT_REC_GENERATION=""
# The reason the current resume step refused, the run directory it resolved, the
# fence it established, and whether the resume came from a tick (a refusal from a
# tick notifies, because the operator is not watching one).
RESUME_REFUSAL=""
RESUME_RUN_DIR=""
RESUME_RESERVATION_ID=""
RESUME_RESERVATION_GENERATION=""
RESUME_FROM_TICK=0
# The stage the launch resumed from, read before the launch so a fast child
# cannot move it out from under the log line and the closing message.
RESUME_STAGE=""
# Set when a refusal must not notify again, because the caller already did.
RESUME_REFUSE_QUIET=0

# Write the halt record beside the marker.
#
# The record is a single slot: a later halt overwrites it, last halt wins. It is
# written to a temporary file in the same folder and moved into place, so a
# reader never sees half of one. The fence the halted run held is read from the
# claim file, because it is the fence candidate a resume starts from.
halt_record_write() {   # <phase> <phase_file> <run_dir> <state> <rc> <source> [reason]
  local marker="" reservation_id="" generation="" rc="$5"
  if [ -f "$HALT" ]; then
    marker="$(tr '\n' ' ' <"$HALT")"
  fi
  if claim_read_fields "$CLAIM"; then
    reservation_id="$CLAIM_ID"
    generation="$CLAIM_GEN"
    if [ "$CLAIM_NUMBER" != "$1" ]; then
      reservation_id=""; generation=""
    fi
  fi
  case "$rc" in
    ""|*[!0-9-]*) rc="" ;;
  esac
  local tmp="$HALT_RECORD.tmp.$$"
  # The rename is done in Python, not with `mv`. The installed-command test runs
  # the driver with a PATH holding only the commands the configuration declares,
  # and `mv` is not one of them.
  "$PYTHON" - "$tmp" "$1" "$2" "$3" "$4" "$rc" "$6" "${7:-}" "$marker" \
    "$reservation_id" "$generation" "$(ts)" "$HALT_RECORD" <<'PY'
import json, os, sys
# argv[13] is the destination. The temp file is written beside it and replaced in
# one step, so a reader never sees half a record.
record = {
    "version": 1,
    "phase": sys.argv[2],
    "phase_file": sys.argv[3],
    "run_dir": sys.argv[4],
    "state": sys.argv[5],
    "rc": int(sys.argv[6]) if sys.argv[6] else None,
    "source": sys.argv[7],
    "reason": sys.argv[8],
    "marker": sys.argv[9],
    "reservation_id": sys.argv[10],
    "generation": sys.argv[11],
    "recorded_at": sys.argv[12],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(sys.argv[1], sys.argv[13])
PY
}

# Read and validate the halt record into the HALT_REC_* globals.
#
# A record that is not version 1, or that carries no phase, phase file or run
# directory, is refused: a resume cannot act on it, so it must not be acted on.
# Returns non-zero with every global empty when the record is unusable.
halt_record_read() {   # <path>
  local out line
  HALT_REC_PHASE=""; HALT_REC_PHASE_FILE=""; HALT_REC_RUN_DIR=""
  HALT_REC_STATE=""; HALT_REC_RC=""
  HALT_REC_RESERVATION_ID=""; HALT_REC_GENERATION=""
  out="$("$PYTHON" - "$1" <<'PY'
import json, sys
keys = ("phase", "phase_file", "run_dir", "state", "rc",
        "reservation_id", "generation")
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        record = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(1)
if not isinstance(record, dict) or record.get("version") != 1:
    raise SystemExit(1)
for key in ("phase", "phase_file", "run_dir"):
    if not isinstance(record.get(key), str) or not record.get(key):
        raise SystemExit(1)
for key in keys:
    value = record.get(key)
    print(f"{key}={'' if value is None else value}")
PY
)" || return 1
  # One `key=value` line per field, not one tab-separated line. A tab is an IFS
  # *whitespace* character, so an empty field between two tabs collapses and every
  # field after it shifts by one. `state` is empty for the interrupted class, so
  # that shift read the record's `rc` as its state.
  while IFS= read -r line; do
    case "$line" in
      phase=*) HALT_REC_PHASE="${line#phase=}" ;;
      phase_file=*) HALT_REC_PHASE_FILE="${line#phase_file=}" ;;
      run_dir=*) HALT_REC_RUN_DIR="${line#run_dir=}" ;;
      state=*) HALT_REC_STATE="${line#state=}" ;;
      rc=*) HALT_REC_RC="${line#rc=}" ;;
      reservation_id=*) HALT_REC_RESERVATION_ID="${line#reservation_id=}" ;;
      generation=*) HALT_REC_GENERATION="${line#generation=}" ;;
    esac
  done <<<"$out"
  return 0
}

# Refuse a resume: write the marker the next tick halts on, and rewrite the
# record so the reason and a fresh timestamp are what a reader sees. The record
# is kept, not consumed, so the next attempt can try again. Only a record for the
# phase being resumed is rewritten: a request for another phase must not clobber
# the halt that is still standing.
resume_refuse() {   # <phase> <reason>
  local phase="$1" reason="$2"
  printf 'resume refused: %s\n' "$reason" >"$HALT"
  if [ -n "$HALT_REC_PHASE" ] && [ "$HALT_REC_PHASE" = "$phase" ]; then
    halt_record_write "$HALT_REC_PHASE" "$HALT_REC_PHASE_FILE" \
      "$HALT_REC_RUN_DIR" "$HALT_REC_STATE" "$HALT_REC_RC" resume-refused "$reason"
  fi
  RESUME_REFUSAL="$reason"
  log "resume refused: phase ${phase:-unknown}: $reason"
  if [ "$RESUME_FROM_TICK" = 1 ] && [ "$RESUME_REFUSE_QUIET" != 1 ]; then
    notify "halt resume refused for phase ${phase:-unknown}: $reason"
  fi
}

# Why a run cannot resume, or nothing when it can.
#
# Two classes resume: a run glide recorded as `blocked`, and a run directory with
# no readable `run.json` but a `resume.json` beside it (an interrupted run).
# Everything else fails closed and names the reason.
resume_gate_reason() {   # <state> <run_dir>
  local state="$1" run_dir="$2"
  if [ "$state" = blocked ]; then
    return 0
  fi
  if [ -z "$state" ] && [ -f "$run_dir/resume.json" ]; then
    return 0
  fi
  case "$state" in
    completed)
      printf 'the run is completed; clear the halt marker to let the queue advance' ;;
    rejected|remedy_escalated)
      printf 'the run ended as %s; that phase needs a decision, not a resume' "$state" ;;
    "")
      printf 'run.json is absent or unreadable and %s has no resume.json' "$run_dir" ;;
    *)
      printf 'the run state %s is not blocked' "$state" ;;
  esac
  return 1
}

# The stage a resume continues from, from the run's own resume state.
resume_stage() {   # <run_dir>
  local file="$1/resume.json"
  [ -f "$file" ] || return 0
  "$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("current",""))' \
    "$file" 2>/dev/null || true
}
