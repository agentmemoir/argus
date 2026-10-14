#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The resume: prove the ledger, hold the board fence, cut the branch, launch.
#
# Every check runs before the launch, so a refusal leaves the run untouched,
# rewrites the record and writes the marker. Nothing here archives the run it
# continues: a resume exists to keep the stage state `--fresh` would throw away.


# The distinct non-empty step pins in the run's ledger, one per line.
resume_ledger_pins() {   # <run_dir>
  local file="$1/ledger.json"
  [ -f "$file" ] || return 0
  "$PYTHON" - "$file" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ledger = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)
steps = ledger.get("steps") if isinstance(ledger, dict) else None
if not isinstance(steps, dict):
    raise SystemExit(0)
seen = []
for entry in steps.values():
    head = entry.get("git_head") if isinstance(entry, dict) else None
    if isinstance(head, str) and head and head not in seen:
        seen.append(head)
for head in seen:
    print(head)
PY
}

# The exact command a person must run when the auto-pin refuses.
resume_repin_hint() {   # <number> <rel>
  local line text="glide run --pipeline $PIPELINE"
  while IFS= read -r line; do
    text="$text $line"
  done < <(glide_input_args "$1" "$2" 2>/dev/null || true)
  printf '%s --repin-head --repin-reason "why HEAD moved"' "$text"
}

# Prove the ledger's step pins still describe the tree, or fast-forward them.
#
# No pins and every pin equal to HEAD both resume untouched. When every pin is an
# ancestor of HEAD the tree only moved forward, so glide is asked to repin once
# and the pins are re-read. Anything else fails closed and names the command a
# person must run: guessing would repin a run whose outputs no longer match the
# tree.
resume_ledger_proof() {   # <run_dir> <number> <rel>
  local run_dir="$1" number="$2" rel="$3" pins head pin all_equal=1 all_ancestor=1
  RESUME_REFUSAL=""
  pins="$(resume_ledger_pins "$run_dir")"
  if [ -z "$pins" ]; then
    return 0
  fi
  head="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$head" ]; then
    RESUME_REFUSAL="HEAD is unreadable in $REPO"
    return 1
  fi
  while IFS= read -r pin; do
    [ "$pin" = "$head" ] || all_equal=0
  done <<<"$pins"
  if [ "$all_equal" = 1 ]; then
    return 0
  fi
  while IFS= read -r pin; do
    git -C "$REPO" merge-base --is-ancestor "$pin" "$head" 2>/dev/null \
      || all_ancestor=0
  done <<<"$pins"
  if [ "$all_ancestor" != 1 ]; then
    RESUME_REFUSAL="the ledger pins a commit HEAD does not descend from; run $(resume_repin_hint "$number" "$rel")"
    return 1
  fi
  local reason pin_args=() line
  reason="argus resume auto-pin: HEAD descends from pinned $(printf '%s' "$pins" | head -1)"
  # A self-test hook. The decision is what is proven here; glide's own repin write
  # is glide's test, and a self-test that ran the real command would need a glide
  # binary and a writable ledger it must not have.
  case "${DRIVER_STUB_REPIN:-}" in
    ok)
      log "resume auto-pin: (stub) would repin from [$(printf '%s' "$pins" | tr '\n' ' ')] to $head; reason: $reason"
      return 0 ;;
    fail)
      RESUME_REFUSAL="the ledger auto-pin failed; run $(resume_repin_hint "$number" "$rel")"
      return 1 ;;
  esac
  while IFS= read -r line; do
    pin_args+=("$line")
  done < <(glide_input_args "$number" "$rel" 2>/dev/null || true)
  if ! "$CMD_GLIDE" run --pipeline "$PIPELINE" "${pin_args[@]}" \
      --repin-head --repin-reason "$reason" >>"$LOG" 2>&1; then
    RESUME_REFUSAL="the ledger auto-pin failed; run $(resume_repin_hint "$number" "$rel")"
    return 1
  fi
  while IFS= read -r pin; do
    if [ "$pin" != "$head" ]; then
      RESUME_REFUSAL="the ledger auto-pin did not settle every pin on HEAD"
      return 1
    fi
  done <<<"$(resume_ledger_pins "$run_dir")"
  log "resume auto-pin: from [$(printf '%s' "$pins" | tr '\n' ' ')] to $head; reason: $reason"
  return 0
}

# One field of the reservation object the board CLI printed.
resume_reservation_field() {   # <field>
  "$PYTHON" - "$1" <<'PY'
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
record = data.get("reservation") if isinstance(data, dict) else None
if not isinstance(record, dict):
    record = data if isinstance(data, dict) else {}
print(record.get(sys.argv[1], ""))
PY
}

# Establish the board fence the resumed run launches under.
#
# The candidates, in order: the claim when it names this phase, then the fence the
# record kept from the halt. The fence is renewed first; when that fails and the
# slot is free, a fresh reservation is taken. Any conflict or unreachable board
# fails closed, because launching without a fence would let a second machine own
# the phase while this one works it.
resume_fence() {   # <phase>
  local phase="$1" candidate_id="" candidate_gen="" claim_number=""
  RESUME_REFUSAL=""
  if [ -s "$CLAIM" ]; then
    IFS=$'\t' read -r claim_number candidate_id candidate_gen <"$CLAIM" || true
    if [ "$claim_number" != "$phase" ]; then
      candidate_id=""; candidate_gen=""
    fi
  fi
  if [ -z "$candidate_id" ]; then
    candidate_id="$HALT_REC_RESERVATION_ID"
    candidate_gen="$HALT_REC_GENERATION"
  fi
  if [ -n "$candidate_id" ] && [ -n "$candidate_gen" ] \
      && resume_fence_renew "$phase" "$candidate_id" "$candidate_gen"; then
    return 0
  fi
  resume_fence_reserve "$phase"
}

# Renew the fence the halted run held, and set the fence globals when it holds.
resume_fence_renew() {   # <phase> <reservation_id> <generation>
  local phase="$1" reservation_id="$2" generation="$3" rc=0
  case "${DRIVER_STUB_BOARD_FENCE:-}" in
    renew)
      RESUME_RESERVATION_ID="$reservation_id"
      RESUME_RESERVATION_GENERATION="$generation"
      return 0 ;;
    reserve|fail) return 1 ;;
  esac
  "$PYTHON" "$ARGUS/board/reservations.py" renew-group \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
    --reservation-id "$reservation_id" --generation "$generation" \
    --status running >"$RUN_DIR/claim-renew-$phase.json" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    return 1
  fi
  RESUME_RESERVATION_ID="$reservation_id"
  RESUME_RESERVATION_GENERATION="$generation"
  return 0
}

# Take a fresh reservation when the halted one is gone and the slot is free.
resume_fence_reserve() {   # <phase>
  local phase="$1" out="" rc=0
  case "${DRIVER_STUB_BOARD_FENCE:-}" in
    reserve)
      RESUME_RESERVATION_ID="rsv-resume-stub"
      RESUME_RESERVATION_GENERATION="9"
      return 0 ;;
    renew|fail)
      RESUME_REFUSAL="the board refused both a renewal and a fresh reservation for phase $phase"
      return 1 ;;
  esac
  out="$("$PYTHON" "$ARGUS/board/reservations.py" reserve \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --phase "$phase" --machine-id "$PHASE_MACHINE_ID" \
    --run-id "phase-$phase" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    RESUME_REFUSAL="the board would not grant a fence for phase $phase: $(printf '%s' "$out" | tr '\n' ' ')"
    return 1
  fi
  RESUME_RESERVATION_ID="$(printf '%s' "$out" | resume_reservation_field reservation_id)"
  RESUME_RESERVATION_GENERATION="$(printf '%s' "$out" | resume_reservation_field generation)"
  if [ -z "$RESUME_RESERVATION_ID" ] || [ -z "$RESUME_RESERVATION_GENERATION" ]; then
    RESUME_REFUSAL="the board returned no fence for phase $phase"
    return 1
  fi
  return 0
}

# The launch path for a phase number, in the repo-relative shape the selector
# reports, so `--session N <file>` receives what it always receives.
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
  # `_drive` clears this before a tick, but `--resume` never runs `_drive`, so a
  # resume clears it itself: `set -u` makes a missing one fatal at the check
  # below, and a stale one would abort a launch that already happened.
  _DRIVE_RC=""

  if ! work_preflight; then
    resume_refuse "$target" "$PREFLIGHT_REASON"
    return 1
  fi

  # Read the record once, before the branch: a named phase takes its fence from
  # it too when it names the same phase. `halt_record_read` clears the fields
  # first, so a resume never acts on fields an earlier read left behind.
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

  run_dir="$HALT_REC_RUN_DIR"
  if [ -z "$run_dir" ]; then
    run_dir="$(phase_run_dir "$number" "$rel")"
  fi
  RESUME_RUN_DIR="$run_dir"

  # The record's own snapshot is the gate when it names this phase: that is the
  # state the halt left, and it is what the record promises a reader. A request
  # for another phase has no snapshot to read, so it asks the run itself. The
  # child re-reads the live state before glide starts, and that is what catches a
  # run that moved on between the two.
  local gate_state
  if [ "$HALT_REC_PHASE" = "$number" ]; then
    gate_state="$HALT_REC_STATE"
  else
    gate_state="$(phase_state "$number" "$rel")"
  fi
  reason="$(resume_gate_reason "$gate_state" "$run_dir")" || {
    resume_refuse "$target" "$reason"
    return 1
  }

  if ! resume_ledger_proof "$run_dir" "$number" "$rel"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  if ! resume_fence "$number"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  if ! _drive_resume_branch "$number" "$rel"; then
    resume_refuse "$target" "$RESUME_REFUSAL"
    return 1
  fi

  reservation_id="$RESUME_RESERVATION_ID"
  reservation_generation="$RESUME_RESERVATION_GENERATION"
  record_claim "$number" "$reservation_id" "$reservation_generation"

  RESUME_MODE=1
  _drive_launch || return $?
  [ -z "${_DRIVE_RC:-}" ] || return "$_DRIVE_RC"

  rm -f "$HALT_RECORD"
  log "halt-clear: phase $number resume launched (stage $(resume_stage "$run_dir"), reservation $reservation_id gen $reservation_generation)"
  return 0
}

# `--resume [N]`: resume the recorded phase, or the named one, while the line
# stays halted. Exits 0 after the launch, without waiting for the run.
resume_cmd() {   # [<number>]
  local requested="${1:-}"
  case "$requested" in
    "") ;;
    *[!0-9]*)
      printf 'argus: --resume takes a phase number, not %s\n' "$requested" >&2
      exit 2 ;;
  esac
  if tmux_busy; then
    printf 'argus: a %s window is already running; stop it first\n' "${work_kind:-phase}" >&2
    exit 1
  fi
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
    "${work_kind:-phase}" "$number" "$(resume_stage "$RESUME_RUN_DIR")"
}
