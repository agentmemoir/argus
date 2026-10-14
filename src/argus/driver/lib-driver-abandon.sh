#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The abandon close-out: park a phase that will not resume.
#
# The residue is what the selector reads to skip and report the phase, so a hand
# tick is no longer how a halted phase is closed out. The run directory is left
# alone: it holds the evidence a person needs to decide what happens next.


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
  if tmux_busy; then
    printf 'argus abandon: a %s window is running; stop it first (make stop)\n' \
      "${work_kind:-phase}" >&2
    exit 1
  fi
  rel="$(resume_phase_file_of "$number")" || true
  run_dir="$(phase_run_dir "$number" "${rel:-}")"
  if [ ! -d "$run_dir" ]; then
    printf 'argus abandon: no run directory at %s; nothing to abandon\n' "$run_dir" >&2
    exit 1
  fi
  state="$(phase_state "$number" "${rel:-}")"

  local claim_number="" fence_id="" fence_gen="" tmp="$run_dir/abandoned.json.tmp.$$"
  if [ -s "$CLAIM" ]; then
    IFS=$'\t' read -r claim_number fence_id fence_gen <"$CLAIM" || true
    if [ "$claim_number" != "$number" ]; then
      fence_id=""; fence_gen=""
    fi
  fi
  "$PYTHON" - "$tmp" "$number" "$rel" "$run_dir" "$state" "$reason" \
    "$fence_id" "$fence_gen" "$(ts)" <<'PY'
import json, sys
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
PY
  mv -f "$tmp" "$run_dir/abandoned.json"

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
