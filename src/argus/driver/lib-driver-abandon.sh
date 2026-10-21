#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The abandon close-out: park a phase that will not resume.
#
# The residue is what the selector reads to skip and report the phase, so a hand
# tick is no longer how a halted phase is closed out. The run directory is left
# alone: it holds the evidence a person needs to decide what happens next.


# Write the abandonment residue atomically beside the run directory.
#
# The temp file is written in the same folder and replaced in one step, so a
# reader never sees half a residue. It is its own function so a check can run the
# writer against confirmed `noexec` storage, with the executable stubs kept
# elsewhere.
abandon_residue_write() {   # <number> <rel> <run_dir> <state> <reason> <id> <gen>
  local tmp="$3/abandoned.json.tmp.$$"
  "$PYTHON" - "$tmp" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$(ts)" \
    "$3/abandoned.json" <<'PY'
import json, os, sys
# argv[10] is the destination; the temp file beside it is replaced in one step.
residue = {
    "version": 1,
    "phase": sys.argv[2],
    "phase_file": sys.argv[3],
    "run_dir": sys.argv[4],
    "state": sys.argv[5],
    "reason": sys.argv[6],
    "reservation_id": sys.argv[7],
    "generation": sys.argv[8],
    "abandoned_at": sys.argv[9],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(residue, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.replace(sys.argv[1], sys.argv[10])
PY
}

# Release the board group, then park the phase by writing the residue, and keep
# the run directory. The marker is left alone, so the line still says it is
# stopped.
# The fence helper is sourced here because abandon runs from the installed
# command as well as the driver, where phase-driver.sh has not sourced it yet.
# shellcheck source=lib-driver-fence.sh
if ! declare -F board_assert_owner >/dev/null 2>&1; then
  _abandon_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck disable=SC1091
  . "$_abandon_dir/lib-driver-fence.sh"
fi
abandon_cmd() {   # <number> [--reason TEXT]
  local number="${1:-}" reason="" rel="" run_dir="" state=""
  shift || true
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --reason)
        if [ "$#" -lt 2 ]; then
          printf 'argus abandon: --reason needs a value\n' >&2
          exit 2
        fi
        reason="$2"; shift 2 ;;
      *)
        printf 'usage: argus abandon <NUMBER> [--reason TEXT]\n' >&2
        exit 2 ;;
    esac
  done
  case "$number" in
    ""|*[!0-9]*)
      printf 'usage: argus abandon <NUMBER> [--reason TEXT]\n' >&2
      exit 2 ;;
  esac
  # The same lock the tick and `--resume` take, held through the residue write,
  # the board release, and the record removal: without it an abandon could race a
  # resume and delete the record that resume just wrote. It is the shell fd 9 and
  # releases on exit.
  driver_lock_take || { printf 'argus abandon: another driver instance holds the lock; try again\n' >&2; exit 1; }
  if tmux_busy; then
    printf 'argus abandon: a %s window is running; stop it first (make stop)\n' \
      "${work_kind:-phase}" >&2
    exit 1
  fi
  # A live recorded pid with no phase window is an orphaned run, not a phase to
  # park. Refusing leaves the marker and the run evidence for the operator.
  driver_orphan_check || { printf 'argus abandon: a phase run is alive with no window (orphaned); not abandoning\n' >&2; exit 1; }
  rel="$(resume_phase_file_of "$number")" || true
  run_dir="$(phase_run_dir "$number" "${rel:-}")"
  if [ ! -d "$run_dir" ]; then
    printf 'argus abandon: no run directory at %s; nothing to abandon\n' "$run_dir" >&2
    exit 1
  fi
  state="$(phase_state "$number" "${rel:-}")"

  local fence_id="" fence_gen=""
  # The residue is a state write, and the board is the authority: a fence the
  # board does not confirm must not park a phase another machine is running.
  # An unreachable board fails closed, so the residue is written only after
  # the fence is proved. An unreadable or phase-mismatched claim proves
  # nothing about who owns the phase, so it is a hard refusal: writing the
  # residue without it would park a phase the board may have given away.
  if ! claim_read_fields "$CLAIM" || [ "$CLAIM_NUMBER" != "$number" ] \
      || [ -z "$CLAIM_ID" ] || [ -z "$CLAIM_GEN" ]; then
    printf 'argus abandon: no readable claim for %s at %s; not abandoning (the claim must name this phase with a full fence)\n' \
      "$number" "$CLAIM" >&2
    exit 1
  fi
  fence_id="$CLAIM_ID"
  fence_gen="$CLAIM_GEN"
  if ! board_assert_owner "$number" "$fence_id" "$fence_gen"; then
    printf 'argus abandon: the board does not confirm the fence for %s; not abandoning (see %s/assert-owner-%s.json)\n' \
      "$number" "$RUN_DIR" "$number" >&2
    exit 1
  fi
  # The release comes before the residue, and a release that fails stops here,
  # so exactly one of two states is left behind: released and parked, or owned
  # and unparked. The other order would park a phase the board still gives this
  # machine, which is a phase no owner will run and no other machine may take.
  # There is deliberately no compensating write: an unpark after a failed
  # release can itself fail, and would leave exactly the state this order
  # avoids. The rename is done in Python, not with `mv`, for the same reason the
  # halt record's is: the installed-command test declares the commands the
  # driver may use, and `mv` is not one of them.
  if [ -n "$fence_id" ] && [ -n "$fence_gen" ]; then
    case "${DRIVER_STUB_BOARD_FENCE:-}" in
      fail)
        printf 'argus abandon: could not release the board group for %s\n' "$number" >&2
        exit 1 ;;
      # These successful fence stubs also stand in for the board release in
      # self-tests; no fixture board claim was written for them.
      assert|renew|reserve)
        rm -f "$CLAIM" ;;
      *)
        if ! "$PYTHON" "$ARGUS/board/reservations.py" release-group \
            "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
            --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
            --reservation-id "$fence_id" --generation "$fence_gen" \
            >"$run_dir/release.json" 2>&1; then
          printf 'argus abandon: could not release the board group for %s; nothing was parked (see %s/release.json)\n' \
            "$number" "$run_dir" >&2
          exit 1
        fi
        rm -f "$CLAIM" ;;
    esac
  fi

  abandon_residue_write "$number" "$rel" "$run_dir" "$state" "$reason" \
    "$fence_id" "$fence_gen"

  if halt_record_read "$HALT_RECORD" && [ "$HALT_REC_PHASE" = "$number" ]; then
    rm -f "$HALT_RECORD"
  fi
  if [ "$(cat "$RUN_DIR/stopped-claim" 2>/dev/null)" = "$number" ]; then
    rm -f "$RUN_DIR/stopped-claim"
  fi
  log "abandoned ${work_kind:-phase} $number (state ${state:-unknown}); the selector reports it under needs_decision"
  printf 'abandoned %s %s; the halt marker is left alone\n' "${work_kind:-phase}" "$number"
}
