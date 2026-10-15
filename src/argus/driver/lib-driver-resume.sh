#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The resume: resolve the target, cut the branch, prove the ledger, hold the
# fence, launch.
#
# The branch is cut before the ledger proof, because the proof proves the pins
# against the launch HEAD and the branch step can move HEAD: proving first left
# the launch on a HEAD the proof never saw. Every check runs before the launch,
# so a refusal leaves the run untouched, rewrites the record and writes the
# marker. Nothing here archives the run it continues: a resume exists to keep
# the stage state `--fresh` would throw away.

resume_phase_file_of() {   # <number>
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.conventions import resolve_phase_file
path = resolve_phase_file(sys.argv[2], int(sys.argv[3]))
if path is None:
    raise SystemExit(1)
try:
    path = path.resolve().relative_to(Path(sys.argv[2]).resolve())
except ValueError:
    pass
print(path)
PY
}

# Put the checkout on the phase branch before the resumed launch.
#
# The tick's own cut also runs a resync, which can move HEAD after the ledger
# proof was taken. A resume proves the pins against HEAD and then must not move
# it, so only the branch half runs here. A refusal is fail-closed: a launch onto
# the integration branch would put unreviewed product code where others pull it.
_drive_resume_branch() {   # <number> <rel>
  local number="$1" rel="$2" roadmap_name
  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    return 0
  fi
  roadmap_name="$(basename "$rel" .md)"
  if ! "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      --mode branch --phase "$number" --roadmap "$roadmap_name" \
      "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
      --roadmap-dir "$(roadmap_dir)" \
      "${GROUP_ARGS[@]+"${GROUP_ARGS[@]}"}" \
      >"$RUN_DIR/phase-branch.json" 2>/dev/null; then
    RESUME_REFUSAL="the branch helper refused (see $RUN_DIR/phase-branch.json); a dirty checkout is the usual cause"
    return 1
  fi
  if group_active; then
    group_record_branch "$(selection_value branch <"$RUN_DIR/phase-branch.json")"
  fi
  log "${work_kind:-phase} $number: $(branch_state_line "$RUN_DIR/phase-branch.json")"
  return 0
}

# Resume the phase a halt stopped, at the stage it blocked on.
#
# Called by the tick when the marker is gone and a record remains, and by
# `--resume [N]`. With no number the record names the phase; with a number that
# phase is resumed, and the record only supplies the fence when it names the same
# phase. Every check runs before the launch, so a refusal leaves the run
# untouched, rewrites the record and writes the marker.
#
# `number`, `rel`, `reservation_id` and `reservation_generation` are assigned
# without `local` on purpose: they are the fence `_drive_launch` reads, and
# `_drive` holds them in scope when the tick calls this.
_drive_resume() {   # [<number>]
  local requested="${1:-}"
  # Separate statements: `local a="$1" b="$a"` expands every argument before the
  # builtin runs, so `$a` would be read before it is set.
  local target="$requested" run_dir reason
  RESUME_REFUSAL=""
  RESUME_REFUSE_QUIET=0
  RESUME_PHASE_FILE=""
  RESUME_LIVE_STATE=""
  # `_drive` clears this before a tick, but `--resume` never runs `_drive`, so a
  # resume clears it itself: `set -u` makes a missing one fatal at the check
  # below, and a stale one would abort a launch that already happened.
  _DRIVE_RC=""

  # Read the record first, before anything can refuse: a refusal rewrites or
  # writes a record for the target, and a named phase takes its fence from it too
  # when it names the same phase. `halt_record_read` clears the fields first, so
  # a resume never acts on fields an earlier read left behind.
  local record_ok=0
  if halt_record_read "$HALT_RECORD"; then
    record_ok=1
  fi

  if [ -n "$requested" ]; then
    number="$requested"
    rel="$(resume_phase_file_of "$number")" || true
    if [ -z "$rel" ]; then
      resume_refuse "$target" "no phase file found for phase $number"
      return 1
    fi
  else
    if [ "$record_ok" != 1 ]; then
      resume_refuse "$target" "the halt record $HALT_RECORD is missing or malformed"
      return 1
    fi
    number="$HALT_REC_PHASE"
    target="$number"
    rel="$HALT_REC_PHASE_FILE"
  fi
  RESUME_PHASE_FILE="$rel"

  # The record is one slot and may belong to another phase: `--resume N` names a
  # phase the record need not. Its run directory, its state snapshot and its
  # fence all belong to the phase it names, so a mismatch clears all three. Read
  # for a different phase, they would prove that phase's ledger and renew that
  # phase's fence while launching this one.
  if [ "$HALT_REC_PHASE" != "$number" ]; then
    HALT_REC_RUN_DIR=""
    HALT_REC_STATE=""
    HALT_REC_RESERVATION_ID=""
    HALT_REC_GENERATION=""
  fi

  run_dir="$HALT_REC_RUN_DIR"
  if [ -z "$run_dir" ]; then
    run_dir="$(phase_run_dir "$number" "$rel" 2>/dev/null)" || run_dir=""
  fi
  RESUME_RUN_DIR="$run_dir"

  if ! work_preflight; then
    resume_refuse "$target" "$PREFLIGHT_REASON"
    return 1
  fi

  # The gate is asked of two states, and both must pass. The record's snapshot is
  # the state the halt left and what the record promises a reader; the live run is
  # the state now and what glide will actually continue. Asking only the snapshot
  # let a run that reached a terminal state after the halt reach the launch, where
  # only the child stopped it - after a `started` notification had gone out. For a
  # phase the record does not name there is no snapshot, and the live run is the
  # only state there is.
  local gate_state live_state
  live_state="$(phase_state "$number" "$rel")"
  RESUME_LIVE_STATE="$live_state"
  gate_state="${HALT_REC_STATE:-$live_state}"
  reason="$(resume_gate_reason "$gate_state" "$run_dir")" || {
    resume_refuse "$target" "$reason"
    return 1
  }
  if [ "$gate_state" != "$live_state" ]; then
    reason="$(resume_gate_reason "$live_state" "$run_dir")" || {
      resume_refuse "$target" "the run moved on since the halt: $reason"
      return 1
    }
  fi

  # The branch is cut before the ledger proof, because the proof proves the pins
  # against the HEAD the launch uses and the branch step can move HEAD. Proving
  # first and cutting after left the launch on a HEAD the proof never saw.
  # `group_active` reads GROUP_MEMBERS only, so the reorder is safe, and every
  # failure path still refuses.
  if ! _drive_resume_branch "$number" "$rel"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  if ! resume_ledger_proof "$run_dir" "$number" "$rel"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  if ! resume_fence "$number"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  reservation_id="$RESUME_RESERVATION_ID"
  reservation_generation="$RESUME_RESERVATION_GENERATION"
  record_claim "$number" "$reservation_id" "$reservation_generation"

  # Read the stage before the launch: the child starts inside `_drive_launch` and
  # can advance `resume.json` before this line is written, and the stage logged
  # here is the acceptance source a person reads.
  local stage
  stage="$(resume_stage "$run_dir")"
  RESUME_STAGE="$stage"

  # The record's identity, taken before the launch: a child that halts again and
  # rewrites the record must survive the consume below.
  local record_identity
  record_identity="$(halt_record_identity "$HALT_RECORD")"

  # Consume the accepted manual stop before launch. A new stop written while
  # the child starts must survive; removing it after launch would erase it.
  local stopped_claim=""
  if [ "$(cat "$RUN_DIR/stopped-claim" 2>/dev/null)" = "$number" ]; then
    stopped_claim="$number"
    rm -f "$RUN_DIR/stopped-claim"
  fi
  RESUME_MODE=1
  _drive_launch
  if [ "${_DRIVE_RC:-0}" != 0 ]; then
    if [ -n "$stopped_claim" ] && [ ! -f "$RUN_DIR/stopped-claim" ]; then
      printf '%s\n' "$stopped_claim" >"$RUN_DIR/stopped-claim"
    fi
    # No window opened, so the record is kept for a retry and the marker goes
    # back. Without the marker the tick-on-clear branch fires again on every tick,
    # which is the automatic retry the plan rules out. The launch already
    # notified, so this refusal does not notify again.
    RESUME_REFUSE_QUIET=1
    resume_refuse "$target" "the launch failed; see $RUN_DIR/driver.log"
    return 1
  fi

  # Consume the record only when it names the launched phase and nothing rewrote
  # it under us: another phase's halt, or a newer halt the child just wrote, must
  # survive.
  if [ "$HALT_REC_PHASE" = "$number" ] && [ -n "$record_identity" ] \
      && [ "$(halt_record_identity "$HALT_RECORD")" = "$record_identity" ]; then
    rm -f "$HALT_RECORD"
  fi
  log "halt-clear: phase $number resume launched (stage $stage, reservation $reservation_id gen $reservation_generation)"
  return 0
}

# `--resume [N]`: resume the recorded phase, or the named one, while the line
# stays halted. Exits 0 after the launch, without waiting for the run.
resume_cmd() {   # [<number>]
  local requested="${1:-}"
  if [ -z "$requested" ] && [ ! -f "$HALT_RECORD" ] && [ -s "$RUN_DIR/stopped-claim" ]; then
    requested="$(cat "$RUN_DIR/stopped-claim")"
  fi
  case "$requested" in
    "") ;;
    *[!0-9]*)
      printf 'argus: --resume takes a phase number, not %s\n' "$requested" >&2
      exit 2 ;;
  esac
  # The same prologue the tick runs, in the same order. `--resume` enters the
  # launch path without running a tick, so without this a second `--resume`, or a
  # resume racing an orphaned run's live pid, could launch the phase twice.
  driver_lock_take || { printf 'argus: another driver instance holds the lock; try again\n' >&2; exit 1; }
  if tmux_busy; then
    printf 'argus: a %s window is already running; stop it first\n' "${work_kind:-phase}" >&2
    exit 1
  fi
  driver_orphan_check || { printf 'argus: a phase run is alive with no window (orphaned); the line is halted\n' >&2; exit 1; }
  if [ -z "$requested" ] && [ ! -f "$HALT_RECORD" ]; then
    printf 'argus: no halt record at %s; nothing to resume\n' "$HALT_RECORD" >&2
    exit 1
  fi
  RESUME_FROM_TICK=0
  if ! _drive_resume "$requested"; then
    printf 'argus: resume refused: %s\n' "$RESUME_REFUSAL" >&2
    exit 1
  fi
  printf 'resumed %s %s at stage %s; the halt marker is untouched\n' \
    "${work_kind:-phase}" "$number" "${RESUME_STAGE:-unknown}"
}
