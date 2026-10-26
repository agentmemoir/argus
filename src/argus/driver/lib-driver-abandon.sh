#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The abandon close-out: park a phase that will not resume.
#
# The residue is what the selector reads to skip and report the phase, so a hand
# tick is no longer how a halted phase is closed out. The run directory is left
# alone: it holds the evidence a person needs to decide what happens next.


# Stage the abandonment residue beside the run directory, flushed to disk.
#
# The staged file is complete before the board release, so the only write left
# after the release is the rename in abandon_residue_commit. The selector reads
# only `abandoned.json`, never the staged file. It is its own function so a
# check can run the writer against confirmed `noexec` storage, with the
# executable stubs kept elsewhere.
abandon_residue_write() {   # <number> <rel> <run_dir> <state> <reason> <id> <gen>
  local tmp="$3/abandoned.json.tmp.$$"
  "$PYTHON" - "$tmp" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$(ts)" \
    "$3/abandoned.json.pending" <<'PY'
import json, os, sys
# argv[10] is the staged file; the temp file beside it is replaced in one step.
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
    handle.flush()
    os.fsync(handle.fileno())
os.replace(sys.argv[1], sys.argv[10])
PY
}

# Park the phase: move the staged residue into place in one step.
abandon_residue_commit() {   # <run_dir>
  "$PYTHON" -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
    "$1/abandoned.json.pending" "$1/abandoned.json"
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
# Print the board's reason that no live claim covers this phase, or nothing
# when one does. It is the board's own read-only `scan`, asked from this
# machine, so `other_owner` means another machine holds a live claim. Any
# failure to ask or to read the answer returns non-zero: the board did not
# answer, which is never the same as "no claim".
abandon_board_reason() {   # <number>
  local out=""
  case "${DRIVER_STUB_BOARD_FENCE:-}" in fail) return 1 ;; esac
  out="$("$PYTHON" "$ARGUS/board/reservations.py" scan \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --phase "$1" --machine-id "$PHASE_MACHINE_ID" \
    2>"$RUN_DIR/abandon-scan-$1.err")" || return 1
  printf '%s' "$out" | "$PYTHON" -c '
import json, sys
data = json.load(sys.stdin)
if data["state"] != "OK":
    sys.exit(1)
for found in data["phases"]:
    if int(found["phase"]) == int(sys.argv[1]):
        print(found["reason"])
' "$1"
}

# Take the park reservation for a phase no machine holds, before the residue is
# written. The board refuses it when another machine holds a live claim, and a
# board that cannot answer refuses it too, so a refused park writes nothing. The
# reservation runs on the heartbeat window, so a park that stops before its
# release expires on its own. Prints the reservation id and its generation.
abandon_park_reserve() {   # <number>
  local out=""
  out="$("$PYTHON" "$ARGUS/board/reservations.py" reserve \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --phase "$1" --machine-id "$PHASE_MACHINE_ID" --status running \
    2>"$RUN_DIR/abandon-park-$1.err")" || return 1
  printf '%s' "$out" | "$PYTHON" -c '
import json, sys
reservation = json.load(sys.stdin)["reservation"]
print(reservation["reservation_id"], reservation["generation"])
' 2>/dev/null
}

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
  if [ "${work_kind:-phase}" = issue ]; then
    # An issue run's input is its frozen task file in issues.task-dir. Without it
    # the run directory cannot be named, so refuse before any state changes.
    rel="$(issue_task_file_of "$number")" || rel=""
    if [ -z "$rel" ]; then
      printf 'argus abandon: issue %s has no task file; the pipeline input %s is missing; nothing abandoned\n' \
        "$number" "${PHASE_FILE_INPUT:-phase_file}" >&2
      exit 1
    fi
  else
    rel="$(resume_phase_file_of "$number")" || true
  fi
  run_dir="$(phase_run_dir "$number" "${rel:-}")"
  if [ ! -d "$run_dir" ]; then
    printf 'argus abandon: no run directory at %s; nothing to abandon\n' "$run_dir" >&2
    exit 1
  fi
  state="$(phase_state "$number" "${rel:-}")"

  local fence_id="" fence_gen="" claimed=0 release=1 reason_on_board=""
  local park=0 park_out="" park_id="" park_gen=""
  # The residue is a state write, and the board is the authority. A fence this
  # machine still holds is proved and released below. With no such fence, the
  # board's own scan decides: a phase no other machine holds live (no record,
  # an expired or terminal one, or this machine's own stale claim) has nothing
  # left to release and is parked; a live claim by another machine, or a board
  # that cannot answer, refuses and writes nothing, so a later rerun can work.
  if claim_read_fields "$CLAIM" && [ "$CLAIM_NUMBER" = "$number" ] \
      && [ -n "$CLAIM_ID" ] && [ -n "$CLAIM_GEN" ]; then
    claimed=1
    fence_id="$CLAIM_ID"
    fence_gen="$CLAIM_GEN"
  fi
  if [ "$claimed" = 0 ] || ! board_assert_owner "$number" "$fence_id" "$fence_gen"; then
    if ! reason_on_board="$(abandon_board_reason "$number")"; then
      printf 'argus abandon: could not ask the board who holds %s; not abandoning (see %s/abandon-scan-%s.err)\n' \
        "$number" "$RUN_DIR" "$number" >&2
      exit 1
    fi
    case "$reason_on_board" in
      no_claim|expired) release=0; park=1 ;;
      terminal) release=0 ;;
      other_owner)
        printf 'argus abandon: another machine holds a live claim on %s; not abandoning\n' "$number" >&2
        exit 1 ;;
      *)
        printf 'argus abandon: the board does not confirm the fence for %s; not abandoning (see %s/assert-owner-%s.json)\n' \
          "$number" "$RUN_DIR" "$number" >&2
        exit 1 ;;
    esac
  fi
  # A park with no release must not overlap a new reservation of the phase by
  # another machine, so the board takes the park reservation before anything is
  # written. A refusal, or a board that cannot answer, stops here.
  if [ "$park" = 1 ]; then
    if ! park_out="$(abandon_park_reserve "$number")"; then
      printf 'argus abandon: could not take a park reservation for %s; not abandoning (see %s/abandon-park-%s.err)\n' \
        "$number" "$RUN_DIR" "$number" >&2
      exit 1
    fi
    park_id="${park_out%% *}"
    park_gen="${park_out#* }"
  fi
  # The residue is staged first: a write that fails stops here with the claim
  # held and nothing released, so the same command can run again. A write after
  # the release could fail with the claim gone, and the phase would run again.
  # A park reservation taken before this stop is not released; it expires on its
  # own.
  if ! abandon_residue_write "$number" "$rel" "$run_dir" "$state" "$reason" \
      "$fence_id" "$fence_gen"; then
    printf 'argus abandon: could not write the residue for %s in %s; nothing was released, and a park reservation expires on its own\n' \
      "$number" "$run_dir" >&2
    exit 1
  fi
  # The release comes before the park, and a release that fails stops here,
  # so exactly one of two states is left behind: released and parked, or owned
  # and unparked. The other order would park a phase the board still gives this
  # machine, which is a phase no owner will run and no other machine may take.
  # There is deliberately no compensating write: an unpark after a failed
  # release can itself fail, and would leave exactly the state this order
  # avoids. The staged file is not a park, so removing it is best effort. The
  # rename is done in Python, not with `mv`, for the same reason the halt
  # record's is: the installed-command test declares the commands the driver
  # may use, and `mv` is not one of them.
  [ "$release" = 0 ] || case "${DRIVER_STUB_BOARD_FENCE:-}" in
    fail)
      rm -f "$run_dir/abandoned.json.pending"
      printf 'argus abandon: could not release the board group for %s\n' "$number" >&2
      exit 1 ;;
    # These successful fence stubs also stand in for the board release in
    # self-tests; no fixture board claim was written for them.
    assert|renew|reserve) ;;
    *)
      if ! "$PYTHON" "$ARGUS/board/reservations.py" release-group \
          "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
          --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
          --reservation-id "$fence_id" --generation "$fence_gen" \
          >"$run_dir/release.json" 2>&1; then
        rm -f "$run_dir/abandoned.json.pending"
        printf 'argus abandon: could not release the board group for %s; nothing was parked (see %s/release.json)\n' \
          "$number" "$run_dir" >&2
        exit 1
      fi ;;
  esac
  # The claim goes only once the phase is parked. A rename that fails after the
  # release is a failure, never an abandon, and the staged residue is kept as
  # the record of it.
  if ! abandon_residue_commit "$run_dir"; then
    printf 'argus abandon: the board no longer holds %s for this machine, but it could not be parked; the staged residue is %s/abandoned.json.pending, and the same command finishes it\n' \
      "$number" "$run_dir" >&2
    exit 1
  fi
  # The park is in place once the residue is renamed, so the park reservation is
  # given back now. If that fails, the reservation expires on its own, and the
  # phase stays parked.
  if [ "$park" = 1 ] && ! "$PYTHON" "$ARGUS/board/reservations.py" release-group \
      "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
      --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
      --reservation-id "$park_id" --generation "$park_gen" \
      >"$run_dir/park-release.json" 2>&1; then
    printf 'argus abandon: parked %s, but its park reservation could not be released; it expires on its own (see %s/park-release.json)\n' \
      "$number" "$run_dir" >&2
  fi
  [ "$claimed" = 0 ] || rm -f "$CLAIM"

  if halt_record_read "$HALT_RECORD" && [ "$HALT_REC_PHASE" = "$number" ]; then
    rm -f "$HALT_RECORD"
  fi
  if [ "$(cat "$RUN_DIR/stopped-claim" 2>/dev/null)" = "$number" ]; then
    rm -f "$RUN_DIR/stopped-claim"
  fi
  log "abandoned ${work_kind:-phase} $number (state ${state:-unknown}); the selector reports it under needs_decision"
  printf 'abandoned %s %s; the halt marker is left alone\n' "${work_kind:-phase}" "$number"
}
