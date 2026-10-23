#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The resume: resolve the target, prove the ledger, hold the fence, cut the
# branch, launch.
#
# The branch is cut before the launch, so the launch uses the phase's own
# branch. Every check runs before the launch, so a refusal leaves the run
# untouched, rewrites the record and writes the marker. Nothing here archives
# the run it continues: a resume exists to keep the stage state `--fresh` would
# throw away.

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
# The tick's own cut also runs a resync. Only the branch half runs here. A
# failure is a repair, not a stop: the phase keeps its claim, the resume
# continues on the branch the checkout already has, and the next tick retries
# the cut. A fence refusal is not a failure: the board does not confirm this
# machine owns the phase, so the resume stops and launches nothing. The group
# comes from _drive_resume_group, which runs before the fence.
_drive_resume_branch() {   # <number> <rel>
  local number="$1" rel="$2" phases_name
  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    return 0
  fi
  phases_name="$(basename "$rel" .md)"
  if ! "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      --mode branch --phase "$number" --phases "$phases_name" \
      "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
      --phases-directory "$(phases_dir)" \
      "${GROUP_ARGS[@]+"${GROUP_ARGS[@]}"}" \
      >"$RUN_DIR/phase-branch.json" 2>/dev/null; then
    local branch_state
    branch_state="$(selection_value state <"$RUN_DIR/phase-branch.json")"
    case "$branch_state" in
      FENCED|COORDINATION_UNAVAILABLE)
        RESUME_REFUSAL="the $branch_state fence refused the branch cut for ${work_kind:-phase} $number; no launch (see $RUN_DIR/phase-branch.json)"
        log "resume: $RESUME_REFUSAL"
        return 1 ;;
    esac
    # An issue has no branch of its own to fall back to, so a resume on the
    # current branch would put issue work on the base branch (belief 4). It is
    # a launch fault: halt again and keep retrying (lib-driver-launchfault.sh).
    if [ "${work_kind:-phase}" = issue ]; then
      log "resume: could not put issue $number on its own branch; no launch, nothing ran on the base branch (see $RUN_DIR/phase-branch.json)"
      launchfault_halt "$number" "$rel" "$RESUME_RUN_DIR" "$branch_state" 1 "" "" \
        "$(launchfault_branch_reason "$(selection_value error <"$RUN_DIR/phase-branch.json")")"
      RESUME_LAUNCH_FAULT=1
      return 1
    fi
    log "resume: could not put ${work_kind:-phase} $number on its own branch; continuing on $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'the current branch'); the next tick retries (see $RUN_DIR/phase-branch.json)"
    return 0
  fi
  if group_active; then
    group_record_branch "$(selection_value branch <"$RUN_DIR/phase-branch.json")"
  fi
  log "${work_kind:-phase} $number: $(branch_state_line "$RUN_DIR/phase-branch.json")"
  return 0
}

# Read the phase's reservation group before the fence.
#
# Every member of a group publishes on the group's one branch, and a resume has
# no selection to read the group from: the tick's resume branch runs before the
# selector, and `--resume` never selects at all. The group comes from the phase's
# own document, which is where the publication reads it, so the branch step
# names the branch the rest of the group's joins expect instead of stacking a
# branch of this member's own. A declaration that cannot be read is refused
# rather than read as "runs alone": a resume that cannot see the group would cut
# exactly the wrong branch. The refusal carries the phases reader's own words,
# which name the document to fix, and the way out of the refusal, because a
# halted line stays halted until the declaration is fixed or the phase is parked.
# It is a local read, so it runs before the fence: a resume that will refuse on
# the declaration takes no reservation, and the refusal names the document.
_drive_resume_group() {   # <number>
  local number="$1"
  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    return 0
  fi
  if [ "${work_kind:-phase}" != issue ] && ! group_take_declared "$number"; then
    RESUME_REFUSAL="the reservation group of phase $number could not be read (${GROUP_ERROR:-no reason reported}); fix the group declaration, or abandon the phase"
    return 1
  fi
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

  # The gate is asked of two states: the record's snapshot and the live run.
  # A terminal or moved state is a repair, not a stop. Glide owns the two-tick
  # repair: it restores the step's output and ledger entry from the run log, or
  # re-runs the step when there is no evidence. Argus passes the launch through
  # and treats `repair_pending` as a normal no-launch tick.
  local gate_state live_state
  live_state="$(phase_state "$number" "$rel")"
  RESUME_LIVE_STATE="$live_state"
  gate_state="${HALT_REC_STATE:-$live_state}"
  reason="$(resume_gate_reason "$gate_state" "$run_dir")" || \
    log "resume gate: ${work_kind:-phase} $number: $reason; continuing on its own branch for the repair"
  if [ "$gate_state" != "$live_state" ]; then
    reason="$(resume_gate_reason "$live_state" "$run_dir")" || \
      log "resume gate: ${work_kind:-phase} $number moved on since the halt: $reason; continuing on its own branch for the repair"
  fi

  if ! _drive_resume_group "$number"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  if ! resume_fence "$number"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  reservation_id="$RESUME_RESERVATION_ID"
  reservation_generation="$RESUME_RESERVATION_GENERATION"
  # The board is the authority: a claim it does not confirm is a refused fence,
  # and a launch under it would run with no ownership fence. The refusal stops
  # the resume here, before the stage read and the launch, and the reason is
  # logged by record_claim and recorded for the operator. The reservation file
  # the resume's own branch step reads is written with the claim; see
  # record_claim_fence.
  if ! record_claim_fence "$number" "$reservation_id" "$reservation_generation"; then
    resume_refuse "$target" "the claim fence was refused for phase $number; no launch (see the driver log)"
    return 1
  fi

  # The branch is cut after the fence and before the launch: the branch step
  # proves the reservation file record_claim_fence just wrote, and the launch
  # lands on the phase's own branch. A gitsync branch failure is a repair: the
  # resume continues on the branch the checkout has, and the next tick retries
  # the cut. A fence refusal stops it.
  RESUME_LAUNCH_FAULT=0
  if ! _drive_resume_branch "$number" "$rel"; then
    [ "$RESUME_LAUNCH_FAULT" = 0 ] || return 1
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

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
