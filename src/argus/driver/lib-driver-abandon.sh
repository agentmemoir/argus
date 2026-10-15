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

# Park the phase, write the residue, release the board group, keep the run
# directory. The marker is left alone, so the line still says it is stopped.
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
  if claim_read_fields "$CLAIM"; then
    fence_id="$CLAIM_ID"
    fence_gen="$CLAIM_GEN"
    if [ "$CLAIM_NUMBER" != "$number" ]; then
      fence_id=""; fence_gen=""
    fi
  fi
  # The rename is done in Python, not with `mv`, for the same reason the halt
  # record's is: the installed-command test declares the commands the driver may
  # use, and `mv` is not one of them.
  abandon_residue_write "$number" "$rel" "$run_dir" "$state" "$reason" \
    "$fence_id" "$fence_gen"

  if [ -n "$fence_id" ] && [ -n "$fence_gen" ]; then
    case "${DRIVER_STUB_BOARD_FENCE:-}" in
      fail)
        printf 'argus abandon: could not release the board group for %s\n' "$number" >&2
        exit 1 ;;
      renew|reserve)
        rm -f "$CLAIM" ;;
      *)
        if ! "$PYTHON" "$ARGUS/board/reservations.py" release-group \
            "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
            --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
            --reservation-id "$fence_id" --generation "$fence_gen" \
            >"$run_dir/release.json" 2>&1; then
          printf 'argus abandon: could not release the board group for %s (see %s/release.json)\n' \
            "$number" "$run_dir" >&2
          exit 1
        fi
        rm -f "$CLAIM" ;;
    esac
  fi

  if halt_record_read "$HALT_RECORD" && [ "$HALT_REC_PHASE" = "$number" ]; then
    rm -f "$HALT_RECORD"
  fi
  log "abandoned ${work_kind:-phase} $number (state ${state:-unknown}); the selector reports it under needs_decision"
  printf 'abandoned %s %s; the halt marker is left alone\n' "${work_kind:-phase}" "$number"
}
