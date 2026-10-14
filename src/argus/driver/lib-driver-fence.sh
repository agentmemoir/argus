#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The board fence: the reservation a resumed run launches under.
#
# Launching with no fence would let a second machine own the phase while this
# one works it, so the fence is renewed from the halt or taken fresh, and any
# conflict fails closed with the board own answer carried into the refusal.

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
