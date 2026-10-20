#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The claim a one-phase `--session` run launches under.
#
# A tick claims on the shared board before tmux starts, and the pane it opens runs
# `--session`. A `--session` started by hand has no tick behind it, and before this
# gate it ran the work with no claim at all, so a second machine could run the same
# phase. This file gives both paths one answer: the run holds a live claim, or it
# does not start.
#
# Sourced by lib-driver-config.sh; not executable on its own.

# One field of a board or group JSON reply on stdin.
#
# The reserve command wraps its record in `reservation`; the group request answers
# an array of records. Both shapes are read here so the caller has one getter.
session_claim_field() {   # <field>
  "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
if isinstance(data, list):
    data = data[0] if data else {}
if not isinstance(data, dict):
    data = {}
record = data.get("reservation")
if isinstance(record, dict):
    data = record
print(data.get(sys.argv[1], ""))
' "$1"
}

# One actionable refusal line for a board reply.
#
# The reply is printed, never stored: a conflict reply carries the holding
# machine's id, and rule 4 says a machine name is not written to a log or a run
# artifact. Only the state, and the seconds a cooldown reports, reach the line.
session_claim_refusal() {   # <number> <reply>
  printf '%s\n' "$2" | "$PYTHON" -c '
import json, re, sys
number = sys.argv[1]
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
if not isinstance(data, dict):
    data = {}
state = str(data.get("state") or "")
error = str(data.get("error") or "")
match = re.search(r"(\d+) second", error)
left = int(match.group(1)) if match else 0
if state == "WAIT_FOR_CLAIM":
    line = (
        f"phase {number} is held by another machine; no launch. "
        "Action: wait for that claim to expire, then run --session again"
    )
elif left or state == "COOLDOWN":
    line = (
        f"phase {number} is in cooldown for {left} more second(s); no launch. "
        "Action: wait, then run --session again"
    )
elif state == "FENCED":
    line = (
        f"phase {number} reservation is stale; no launch. "
        "Action: run argus --release, then --session again"
    )
elif state == "ALREADY_COMPLETED":
    line = f"phase {number} is already completed; no launch"
elif state == "GROUP_UNAVAILABLE":
    line = (
        f"the reservation group for phase {number} cannot be read; no launch. "
        "Action: fix the phase group declaration, then retry"
    )
else:
    line = (
        f"the board refused the claim for phase {number}; no launch. "
        "Action: check the board and retry"
    )
print(line)
' "$1"
}

# The claim this machine already holds on one phase, as `id<TAB>generation`.
#
# A session that ends without releasing leaves its claim on the board. A second
# start on the same machine is the operator running the phase again, not a rival
# owner, so the claim is reused rather than reported as a conflict with itself. A
# record owned elsewhere, or one already expired, prints nothing.
session_claim_own() {   # <number>
  local state=""
  state="$("$PYTHON" "$ARGUS/board/reservations.py" show \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" 2>/dev/null)" || return 0
  printf '%s\n' "$state" | "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
record = (data.get("phases") or {}).get(sys.argv[1]) if isinstance(data, dict) else None
if not isinstance(record, dict):
    raise SystemExit(0)
if record.get("status") not in {"claimed", "running", "paused", "blocked"}:
    raise SystemExit(0)
print(str(record.get("reservation_id", "")) + "\t" + str(record.get("generation", "")))
' "$1"
}

# Hold a live claim for one `--session` run, or refuse to start it.
#
# Under a tick the parent already claimed: the fence it passed in the environment
# is verified against the board, so a claim that moved between the launch and this
# process is refused rather than run under a fence that no longer owns the phase.
#
# Started by hand there is no parent, so the claim is taken here: the phase's
# declared group when it has one, the phase alone otherwise. The fence is written
# with `record_claim`, which is what --release, --stop, abandon and --resume
# already read, and the two values are exported so the run marks the claim
# running at its launch.
session_claim_ensure() {   # <number> <rel>
  local number="$1" rc=0 out="" id="" generation=""
  if [ -z "${PHASE_MACHINE_ID:-}" ]; then
    printf 'argus: machine id is empty; set machine.id-file, then run --session again\n' >&2
    return 1
  fi
  if [ -n "${CLIO_RESERVATION_ID:-}" ] && [ -n "${CLIO_RESERVATION_GENERATION:-}" ]; then
    if ! out="$("$PYTHON" "$ARGUS/board/reservations.py" assert-owner \
        "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
        --repo-root "$REPO" --phase "$number" \
        --machine-id "$PHASE_MACHINE_ID" \
        --reservation-id "$CLIO_RESERVATION_ID" \
        --generation "$CLIO_RESERVATION_GENERATION" 2>/dev/null)"; then
      printf 'argus: %s\n' "$(session_claim_refusal "$number" "$out")" >&2
      return 1
    fi
    id="$CLIO_RESERVATION_ID"
    generation="$CLIO_RESERVATION_GENERATION"
  else
    # A claim this machine already holds on the phase is this run's own claim: a
    # session that ended without releasing, or a second manual start, must not be
    # refused as a conflict with itself. The board is the truth, so the fence is
    # read from it rather than from a claim file an earlier run may have left.
    local own=""
    own="$(session_claim_own "$number")"
    if [ -n "$own" ]; then
      id="${own%%$'\t'*}"
      generation="${own#*$'\t'}"
    fi
    if [ -n "$id" ] && [ -n "$generation" ]; then
      :
    else
      if ! group_take_declared "$number"; then
        printf 'argus: the reservation group for phase %s cannot be read: %s; no launch\n' \
          "$number" "$GROUP_ERROR" >&2
        return 1
      fi
      if group_active; then
        out="$("$PYTHON" "$ARGUS/select/group.py" "${ARGUS_CONFIG_ARGS[@]}" \
          --phase "$number" --machine-id "$PHASE_MACHINE_ID" 2>/dev/null)" || rc=$?
      else
        out="$("$PYTHON" "$ARGUS/board/reservations.py" reserve \
          "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
          --repo-root "$REPO" --phase "$number" \
          --machine-id "$PHASE_MACHINE_ID" --run-id "phase-$number" 2>/dev/null)" || rc=$?
      fi
      if [ "$rc" -ne 0 ]; then
        printf 'argus: %s\n' "$(session_claim_refusal "$number" "$out")" >&2
        return 1
      fi
      id="$(printf '%s\n' "$out" | session_claim_field reservation_id)"
      generation="$(printf '%s\n' "$out" | session_claim_field generation)"
      if [ -z "$id" ] || [ -z "$generation" ]; then
        printf 'argus: the board returned no reservation for phase %s; no launch. Action: retry\n' \
          "$number" >&2
        return 1
      fi
    fi
  fi
  CLIO_RESERVATION_ID="$id"
  CLIO_RESERVATION_GENERATION="$generation"
  export CLIO_RESERVATION_ID CLIO_RESERVATION_GENERATION
  record_claim "$number" "$id" "$generation"
  return 0
}

# Mark the held claim running, immediately before the work starts.
#
# A claim is taken as `claimed` and only becomes `running` when work begins, so a
# run that never launched leaves a claim the queue can take back. The renewal is
# logged when it fails and never halts: the next tick's renew tries again.
session_claim_start_running() {   # <number>
  [ -n "${CLIO_RESERVATION_ID:-}" ] || return 0
  [ -n "${CLIO_RESERVATION_GENERATION:-}" ] || return 0
  [ -n "${PHASE_MACHINE_ID:-}" ] || return 0
  local rc=0
  "$PYTHON" "$ARGUS/board/reservations.py" renew-group \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" \
    --repo-root "$REPO" --machine-id "$PHASE_MACHINE_ID" \
    --reservation-id "$CLIO_RESERVATION_ID" \
    --generation "$CLIO_RESERVATION_GENERATION" --status running \
    >/dev/null 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    log "claim start: could not mark the claim for ${work_kind:-phase} $1 running (rc=$rc); the launch continues"
  fi
  return 0
}
