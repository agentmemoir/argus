#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The board fence: the reservation a resumed run launches under.
#
# Launching with no fence would let a second machine own the phase while this
# one works it, so the fence is renewed from the halt or taken fresh, and any
# conflict fails closed with the board own answer carried into the refusal.

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
    "$timeout_cmd" 20 "$PYTHON" "$ARGUS/board/reservations.py" assert-owner \
      "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
      --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
      --phase "$1" --reservation-id "$2" \
      --generation "$3" \
      >"$RUN_DIR/assert-owner-$1.json" 2>&1
  else
    "$PYTHON" "$ARGUS/board/reservations.py" assert-owner \
      "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
      --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
      --phase "$1" --reservation-id "$2" \
      --generation "$3" \
      >"$RUN_DIR/assert-owner-$1.json" 2>&1
  fi
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
