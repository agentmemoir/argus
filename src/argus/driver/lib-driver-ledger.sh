#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The ledger proof: prove the run step pins still describe the tree, or
# fast-forward them once.
#
# A resume continues a run whose steps recorded the commit each one ran on. When
# HEAD has moved past those pins the run outputs no longer describe the tree, so
# the proof is conservative: only a pin HEAD descends from may be repinned, and
# only by asking glide to do it. Anything else fails closed and names the command
# a person must run.

# The distinct non-empty step pins in the run's ledger, one per line.
#
# Exit 0 with the pins, or with nothing when there is no ledger at all. Exit 2
# when a ledger is there and cannot be read: the plan resumes on *no* pins, and a
# ledger that will not parse is not an empty one. Reading the two the same way
# let a resume continue with the pins unproven.
resume_ledger_pins() {   # <run_dir>
  local file="$1/ledger.json"
  [ -f "$file" ] || return 0
  "$PYTHON" - "$file" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        ledger = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(2)
steps = ledger.get("steps") if isinstance(ledger, dict) else None
if not isinstance(steps, dict):
    raise SystemExit(2)
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
  local pins_rc=0
  RESUME_REFUSAL=""
  pins="$(resume_ledger_pins "$run_dir")" || pins_rc=$?
  if [ "$pins_rc" -ne 0 ]; then
    RESUME_REFUSAL="the ledger at $run_dir/ledger.json is unreadable; repair it, or abandon the phase"
    return 1
  fi
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
  local reason pin_args=() line stub=0 settled settled_rc=0
  reason="argus resume auto-pin: HEAD descends from pinned $(printf '%s' "$pins" | head -1)"
  # Self-test hooks. `ok` proves the decision, because glide's own repin write is
  # glide's test and a self-test that ran the real command would need a glide
  # binary and a writable ledger it must not have. `unsettled` stubs the command
  # but leaves the ledger alone, so the settle check below is the thing under
  # test: a repin that did not land must refuse, not resume.
  case "${DRIVER_STUB_REPIN:-}" in
    ok)
      log "resume auto-pin: (stub) would repin from [$(printf '%s' "$pins" | tr '\n' ' ')] to $head; reason: $reason"
      return 0 ;;
    unsettled)
      log "resume auto-pin: (stub) the repin left the pins unsettled; reason: $reason"
      stub=1 ;;
    fail)
      RESUME_REFUSAL="the ledger auto-pin failed; run $(resume_repin_hint "$number" "$rel")"
      return 1 ;;
  esac
  if [ "$stub" != 1 ]; then
    while IFS= read -r line; do
      pin_args+=("$line")
    done < <(glide_input_args "$number" "$rel" 2>/dev/null || true)
    if ! "$CMD_GLIDE" run --pipeline "$PIPELINE" "${pin_args[@]}" \
        --repin-head --repin-reason "$reason" >>"$LOG" 2>&1; then
      RESUME_REFUSAL="the ledger auto-pin failed; run $(resume_repin_hint "$number" "$rel")"
      return 1
    fi
  fi
  settled="$(resume_ledger_pins "$run_dir")" || settled_rc=$?
  if [ "$settled_rc" -ne 0 ]; then
    RESUME_REFUSAL="the ledger at $run_dir/ledger.json is unreadable after the auto-pin"
    return 1
  fi
  while IFS= read -r pin; do
    if [ "$pin" != "$head" ]; then
      RESUME_REFUSAL="the ledger auto-pin did not settle every pin on HEAD"
      return 1
    fi
  done <<<"$settled"
  log "resume auto-pin: from [$(printf '%s' "$pins" | tr '\n' ' ')] to $head; reason: $reason"
  return 0
}

# One field of the reservation object the board CLI printed.
