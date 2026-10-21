#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The board fence: the reservation a resumed run launches under.
#
# Launching with no fence would let a second machine own the phase while this
# one works it, so the fence is renewed from the halt or taken fresh, and any
# conflict fails closed with the board own answer carried into the refusal.

# The bound on one board call, in seconds. It is the argument this file has
# always given `timeout`, so a host with a timer behaves exactly as before; the
# override exists for the test that proves the portable bound below.
ARGUS_FENCE_TIMEOUT_S="${ARGUS_FENCE_TIMEOUT_S:-20}"

resume_reservation_field() {   # <field>  (JSON on stdin)
  # `-c`, not a heredoc: a heredoc is the program itself, so `sys.stdin` would
  # be the program text and the piped reply would never be read.
  "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
record = data.get("reservation") if isinstance(data, dict) else None
if not isinstance(record, dict):
    record = data if isinstance(data, dict) else {}
print(record.get(sys.argv[1], ""))
' "$1"
}

# Establish the board fence the resumed run launches under.
#
# The candidates, in order: the claim when it names this phase, then the fence the
# record kept from the halt. The fence is renewed first; when that fails and the
# slot is free, a fresh reservation is taken. Any conflict or unreachable board
# fails closed, because launching without a fence would let a second machine own
# the phase while this one works it.
resume_fence() {   # <phase>
  local phase="$1" candidate_id="" candidate_gen=""
  RESUME_REFUSAL=""
  if claim_read_fields "$CLAIM" && [ "$CLAIM_NUMBER" = "$phase" ]; then
    candidate_id="$CLAIM_ID"
    candidate_gen="$CLAIM_GEN"
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
#
# The board call is behind a hook so a self-test can feed a real-shaped reply
# through the real parser without a board. stderr is kept out of stdout, so a
# diagnostic line cannot be parsed as the reply.
resume_fence_reserve_cmd() {   # <phase>
  case "${DRIVER_STUB_BOARD_REPLY:-}" in
    "") "$PYTHON" "$ARGUS/board/reservations.py" reserve \
          "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
          --repo-root "$REPO" --phase "$1" --machine-id "$PHASE_MACHINE_ID" \
          --run-id "phase-$1" ;;
    *) printf '%s\n' "$DRIVER_STUB_BOARD_REPLY" ;;
  esac
}

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
  out="$(resume_fence_reserve_cmd "$phase" 2>"$RUN_DIR/fence-reserve-$phase.err")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    RESUME_REFUSAL="the board would not grant a fence for phase $phase: $(printf '%s' "$out" | tr '\n' ' ')"
    return 1
  fi
  RESUME_RESERVATION_ID="$(printf '%s' "$out" | resume_reservation_field reservation_id)"
  RESUME_RESERVATION_GENERATION="$(printf '%s' "$out" | resume_reservation_field generation)"
  if [ -z "$RESUME_RESERVATION_ID" ] || [ -z "$RESUME_RESERVATION_GENERATION" ]; then
    RESUME_REFUSAL="the board returned no fence for phase $phase: $(printf '%s' "$out" | tr '\n' ' ')"
    return 1
  fi
  return 0
}

# Record the claim and the reservation file it proves, in that order.
#
# One call, because the two are one fact in two shapes. `record_claim` writes the
# shell's own claim file, which only this driver reads; every fenced write path
# reads `runs/<phase>/reservation.json` and asks the board whether that machine,
# reservation id and generation still own the phase. A tick that recorded only
# the shell copy would hold the phase and prove nothing to the steps it is about
# to call: its own tidy, branch, clean and checkpoint steps would refuse it, and
# the refusal would name a fence the tick believes it has.
#
# The claim is written first and its refusal is returned unchanged: a claim the
# board did not confirm must not leave a reservation file behind that a later
# tick would read as ownership.
#
#   $1 the phase   $2 the reservation id   $3 the generation
record_claim_fence() {
  record_claim "$1" "$2" "$3" || return $?
  record_phase_reservation "$1" "$2" "$3"
}

# Write the confirmed claim where the fenced write paths read it.
#
# Best effort, and silent when the run folder cannot be named: a fence that is
# missing is refused loudly by the path that reads it, and a claim is not
# something this write may take back. A write that fails where the folder could
# be named is said out loud, because it is the difference between a fence that is
# there and one that only looks as though it is.
#
# The values are the ones the board has just confirmed, and the ids and the
# generation are checked to be the shapes that cannot break the JSON. Anything
# else writes nothing: a half-written reservation file would be read as a fence.
#
#   $1 the phase   $2 the reservation id   $3 the generation
record_phase_reservation() {
  local phase="$1" reservation_id="$2" generation="$3" dir="" file=""
  case "$phase" in ''|*[!0-9]*) return 0 ;; esac
  case "$generation" in ''|*[!0-9]*) return 0 ;; esac
  [ -n "$reservation_id" ] || return 0
  dir="$(phase_run_dir "$phase" "" 2>/dev/null)" || return 0
  [ -n "$dir" ] || return 0
  file="$dir/reservation.json"
  mkdir -p "$dir" 2>/dev/null || true
  if ! printf '{\n  "phase": "%s",\n  "machine_id": "%s",\n  "reservation_id": "%s",\n  "generation": %s\n}\n' \
      "$phase" "${PHASE_MACHINE_ID:-}" "$reservation_id" "$generation" >"$file" 2>/dev/null; then
    log "claim: could not write the reservation file for ${work_kind:-phase} $phase (see $file); the fence reads nothing"
  fi
}

# Verify the three-part fence against the board before a state write.
#
# The claim file alone proves nothing: an expired, released, or taken-over
# reservation still looks live to a machine that held it once. The board is
# the authority, and a board that cannot answer fails closed: the write is
# refused rather than made on an unverified fence. Returns 0 only when the
# board confirms the fence.
# Run the board assert-owner call under a bounded timeout, so a hung board
# cannot hold the driver lock (fd 9) forever. Any non-zero status (including a
# timeout kill) is a refusal: the caller must treat the fence as unproved.
board_assert_owner_cmd() {   # <phase> <reservation_id> <generation>
  local timeout_cmd=""
  if command -v timeout >/dev/null 2>&1; then
    timeout_cmd="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    timeout_cmd="gtimeout"
  fi
  if [ -n "$timeout_cmd" ]; then
    "$timeout_cmd" "$ARGUS_FENCE_TIMEOUT_S" "$PYTHON" "$ARGUS/board/reservations.py" assert-owner \
      "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
      --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
      --phase "$1" --reservation-id "$2" \
      --generation "$3" \
      >"$RUN_DIR/assert-owner-$1.json" 2>&1
    return
  fi
  board_assert_owner_bounded "$@"
}

# The portable bound, for a host with no `timeout` and no `gtimeout`.
#
# The board call runs in the background so its pid can be polled without a busy
# spin; the child's stdout and stderr still land in the same file and its own
# status is still returned, so the normal path is unchanged. A child still alive
# after ARGUS_FENCE_TIMEOUT_S is sent SIGTERM, re-checked briefly, then killed,
# and the call refuses. No path returns with the child alive.
board_assert_owner_bounded() {   # <phase> <reservation_id> <generation>
  local out="$RUN_DIR/assert-owner-$1.json" pid="" ticks=0 limit=0 grace=0
  limit=$((ARGUS_FENCE_TIMEOUT_S * 5))
  "$PYTHON" "$ARGUS/board/reservations.py" assert-owner \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
    --phase "$1" --reservation-id "$2" \
    --generation "$3" \
    >"$out" 2>&1 &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$ticks" -ge "$limit" ]; then
      kill -TERM "$pid" 2>/dev/null || true
      # A short re-check, not a second bound: the usual case is that the child
      # dies at once, and one that ignores SIGTERM is killed outright.
      while kill -0 "$pid" 2>/dev/null && [ "$grace" -lt 5 ]; do
        sleep 0.2
        grace=$((grace + 1))
      done
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      printf 'the board did not answer within %ss; the fence is unproved\n' \
        "$ARGUS_FENCE_TIMEOUT_S" >>"$out"
      return 124
    fi
    sleep 0.2
    ticks=$((ticks + 1))
  done
  wait "$pid"
}

board_assert_owner() {   # <phase> <reservation_id> <generation>
  local phase="$1" reservation_id="$2" generation="$3"
  [ -n "$reservation_id" ] && [ -n "$generation" ] || return 1
  # The phase names the run state and the board key; a non-number here would
  # read a path or a key the board does not own, so the fence is refused.
  [[ $phase =~ ^[0-9]+$ ]] || return 1
  mkdir -p "$RUN_DIR"
  case "${DRIVER_STUB_BOARD_FENCE:-}" in
    fail) return 1 ;;
    # Successful fence stubs stand in for their matching board response.
    renew|reserve|assert) return 0 ;;
  esac
  board_assert_owner_cmd "$phase" "$reservation_id" "$generation"
}

# The launch path for a phase number, in the repo-relative shape the selector
# reports, so `--session N <file>` receives what it always receives.
