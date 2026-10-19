#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The group side of a tick: the array the selection carries, the one branch the
# group's work lives on, and the heartbeat renewal the tick loop owes it.
#
# Sourced by phase-driver.sh; not executable on its own.
#
# A reservation group is several phases that one owner claims and runs as one
# unit. One branch and one pull request cover every member, each member keeps its
# own receipt, and only one run is active per machine, so a group's members are
# launched one per tick. What brings the next member back to this machine is the
# claim itself: the board holds the whole group under one reservation id, and the
# tick that finds this machine already holding it recovers its own claim on the
# lowest unfinished member.
#
# The group is read from the selection on every tick rather than remembered from
# the last one. A machine that takes a group over from a dead owner has never
# seen that group before, and a group's branch name and member list have to come
# out of the same declared array whichever tick asks.

# Every string in one JSON array, one per line, or nothing.
#
# The one-value getter beside this reads a scalar. An array printed as a single
# value would be an unparseable blob, and the group is an array by definition.
selection_list() {
  local key="$1"
  "$PYTHON" -c 'import json, sys
try:
    value = json.load(sys.stdin).get(sys.argv[1], [])
except ValueError:
    value = []
if isinstance(value, list):
    for item in value:
        print(item)
' "$key"
}

# Take the group out of one selection, or leave the tick on a single phase.
#
# Sets GROUP_MEMBERS to the declared members, one per line, and GROUP_ARGS to
# the repeated `--group` flags the branch helper takes. A selection that carries
# no group leaves both empty, which is the single-phase tick this driver ran
# before groups existed.
#
# The declared order the selection reports is kept as it arrives. The group's
# branch name is derived from its lowest member, so the order here decides no
# branch; keeping it means the record reads the same on every tick regardless of
# which member this tick happens to start from.
group_take_selection() {
  GROUP_MEMBERS=""
  GROUP_ARGS=()
  local member
  while IFS= read -r member; do
    [ -n "$member" ] || continue
    GROUP_MEMBERS="${GROUP_MEMBERS}${member}"$'\n'
    GROUP_ARGS+=(--group "$member")
  done < <(printf '%s' "$1" | selection_list group)
  return 0
}

# Take the group a phase's own document declares, for a path with no selection.
#
# The tick reads the group from the selection, and a resume has none: the tick's
# resume branch runs before the selector, and `--resume` never selects at all.
# Every member of a group publishes on the group's one branch, so a resume that
# cut a branch of its own would put the member's receipt on a branch none of the
# group's joins know. The declaration is where the publication reads the group
# from, so both name the same branch.
#
# A declaration that cannot be read is refused rather than read as "runs alone":
# a resume that cannot see the group would cut exactly the wrong branch.
#
# Sets GROUP_ERROR to the phases reader's own words when the declaration cannot
# be read. The refusal an operator reads is the only signal a halted line gives,
# and the reader's message names the document to fix, so it travels with the
# refusal instead of stopping at the driver's error stream.
group_take_declared() {   # <number>
  local declared
  GROUP_ERROR=""
  if ! declared="$("$PYTHON" - "$ARGUS" "$REPO" "$1" "$(phases_dir)" \
    "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import json
import sys

sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions

conventions.configure_from_cli(sys.argv[5:])
from argus.phases import source as phases_source

try:
    group = phases_source.resolve_group_or_none(
        sys.argv[2], sys.argv[4], int(sys.argv[3])
    )
except phases_source.ReservationGroupError as exc:
    # The reason goes to stdout, which is what the caller reads. On stderr it
    # would be lost to the caller and the refusal would keep the vaguer words
    # below. A fault this reader did not expect still reaches stderr, as a
    # traceback, which is where a stack trace belongs.
    print(str(exc))
    raise SystemExit(1)
print(json.dumps({"group": list(group["group"]) if group else []}))
PY
)"; then
    GROUP_ERROR="$declared"
    return 1
  fi
  group_take_selection "$declared"
}

# Whether this tick is running a declared group.
group_active() {
  [ -n "${GROUP_MEMBERS:-}" ]
}

# The phases file of the phase this tick launches.
#
# A group's selection names the member to launch, and that member's own file is
# what the launch needs. The selection's `phase` and its `path` are separate
# fields: `phase` is the member the board named, and `path` is the file of the
# phase the selector was asked about. A launch that takes the member from one
# field and its file from the other would run one member's inputs as another
# member's phase, so the member's number is resolved to its own document here.
#
# Prints the file, or nothing when the member has no document, which the caller
# treats as a refusal rather than a launch.
phase_file_of() {
  local number="$1"
  "$PYTHON" - "$ARGUS" "$REPO" "$number" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.conventions import resolve_phase_file
print(resolve_phase_file(sys.argv[2], int(sys.argv[3])) or "")
PY
}

# Resolve the member this tick launches to its own phases file.
#
# Sets GROUP_MEMBER_REL and returns non-zero when the member has no document. A
# member whose file cannot be found is the one failure this tick cannot recover
# from on its own: the launch has to name a file, and guessing one runs another
# phase's inputs.
group_resolve_member() {
  GROUP_MEMBER_REL="$(phase_file_of "$1")"
  [ -n "$GROUP_MEMBER_REL" ]
}

# Record the group this tick is running, so a later tick renews the right claim.
#
# The members are written on every launch, stubbed or not, so a reader can see
# what the run was for. A launch that is not part of a group clears both files,
# because the machine is no longer holding a group. The branch is written
# separately, by the tick that cut it: a launch that never reached the branch
# helper has no name to record.
group_record() {
  if ! group_active; then
    rm -f "$GROUPFILE" "$GROUPBRANCH"
    return 0
  fi
  printf '%s' "$GROUP_MEMBERS" >"$GROUPFILE"
}

group_record_branch() {
  local branch="${1:-}"
  if [ -z "$branch" ]; then
    rm -f "$GROUPBRANCH"
    return 0
  fi
  printf '%s\n' "$branch" >"$GROUPBRANCH"
}

# The branch name the branch helper reported, or nothing.
group_branch_of() {
  [ -s "$GROUPBRANCH" ] || return 0
  head -1 "$GROUPBRANCH"
}

# The `--branch` flag the sync gate takes, when this tick knows the branch.
#
# Only for a group, and only once the branch exists in this checkout. The gate
# asserts that a named branch is the one checked out, so naming a branch that
# does not exist yet would refuse the first tick of every group, which is the
# tick that is supposed to create it.
sync_branch_args() {
  SYNC_BRANCH_ARGS=()
  group_active || return 0
  local branch
  branch="$(group_branch_of)"
  [ -n "$branch" ] || return 0
  "$CMD_GIT" -C "$REPO" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null 2>&1 \
    || return 0
  SYNC_BRANCH_ARGS=(--branch "$branch")
  return 0
}

# Renew the claim this machine holds, while it still owes that claim work.
#
# A claim is measured from its newest heartbeat once work has begun, rather than
# from a fixed deadline, so a claim nobody renews expires under a live run and
# another machine may take it away. Two things keep it alive: a live phase
# window, which is a run in progress, and a recorded group, because a group's
# members chain across ticks and a group waiting on a merge is still this
# machine's to finish. A single phase with no live window is not renewed: nothing
# is working on it, so it should go back to the queue.
#
# The fence is read from what the launch wrote, not from this tick's selection,
# because this runs before the selection is asked: a tick that finds a window
# already open skips the selection entirely. The record is the same pair the
# launch wrote, so the renewal uses the same fence the run holds.
#
# A failure is logged and never halts. A board that cannot be reached is reported
# by the next write that needs it, and stopping a run that is still doing its work
# would lose more than it saves. Both paths log, so a reader can tell "renewed"
# from "tried and could not".
claim_renew() {
  [ -s "$CLAIM" ] || return 0
  if ! tmux_busy && [ ! -s "$GROUPFILE" ]; then
    return 0
  fi
  local claim_number reservation_id generation rc=0
  IFS=$'\t' read -r claim_number reservation_id generation <"$CLAIM" || true
  [ -n "$reservation_id" ] && [ -n "$generation" ] || return 0
  "$PYTHON" "$ARGUS/board/reservations.py" renew-group \
    "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" --repo-root "$REPO" \
    --machine-id "$PHASE_MACHINE_ID" \
    --reservation-id "$reservation_id" \
    --generation "$generation" --status running \
    >"$RUN_DIR/claim-renew${claim_number:+-$claim_number}.json" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    # A failure ends the run of identical successes, so the renewal that follows
    # it is logged even when its words match the one before.
    rm -f "$RUN_DIR/log-last-renew"
    log "claim renew: could not renew the claim for ${work_kind:-phase} $claim_number (rc=$rc; see $RUN_DIR/claim-renew${claim_number:+-$claim_number}.json)"
  elif [ -s "$GROUPFILE" ]; then
    log_changed renew "group renew: renewed the group for ${work_kind:-phase} $claim_number under reservation $reservation_id"
  else
    log_changed renew "claim renew: renewed the claim for ${work_kind:-phase} $claim_number under reservation $reservation_id"
  fi
  return 0
}

# Run the group gate for the pull request a published phase is waiting on.
#
# The gate merges the fix pull requests waiting on the group's branch and makes
# the group's pull request ready once its checks pass. It cannot be a step of
# any one phase, because the checks it waits on finish after every phase has: a
# publish that finds them running holds the pull request in draft, and the first
# tick that finds them green readies it. A group's members share one pull
# request, so the gate runs once per pull request, on the first member the merge
# check lists.
#
# The answer is logged when it changes. A pull request held for running checks
# says so once, and the tick that finds the checks green says so once, rather
# than every tick for as long as the group waits for its merge. A gate that gave
# no answer is logged the same way, with the file that holds its error.
# Retained, uncalled: the tick no longer runs the GitHub gate.
gate_group_pull_request() {
  local phase="$1" pr="$2" out summary last
  [ -n "$pr" ] || return 0
  [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 0
  case " $GATED_PULL_REQUESTS " in *" $pr "*) return 0 ;; esac
  GATED_PULL_REQUESTS="$GATED_PULL_REQUESTS $pr"
  out="$RUN_DIR/group-gate-$phase.json"
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --mode ready --phase "$phase" >"$out" 2>"$out.err" || true
  # `ready` is present only on the tick that performs the transition. An
  # already-ready group returns READY without it, so later ticks may launch.
  if [ "$(selection_value ready <"$out")" != "" ] \
      && [ "$(selection_value state <"$out")" = READY ]; then
    PENDING_READY=$((PENDING_READY + 1))
  fi
  [ "$(selection_value state <"$out")" != NO-GROUP ] || return 0
  summary="$(selection_value summary <"$out")"
  [ -n "$summary" ] || summary="no answer (see $out.err)"
  last="$RUN_DIR/group-gate-$phase.last"
  [ "$(cat "$last" 2>/dev/null)" != "$summary" ] || return 0
  log "group gate for ${work_kind:-phase} $phase: $summary"
  printf '%s\n' "$summary" >"$last"
}

# Read a phase's pull request once per tick. The mode records each alert and
# review comment change in the phase run folder, so a change is returned on the
# first tick that sees it. This logs the kinds it returned. A read that failed
# is logged once, as not read, so it never looks like no change.
# Retained, uncalled: the tick no longer reads pull request signals.
watch_signals_pull_request() {
  local phase="$1" out summary
  [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 0
  out="$RUN_DIR/signals-$phase.json"
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --mode signals --phase "$phase" >"$out" 2>"$out.err" || true
  case "$(selection_value state <"$out")" in
    WATCHED) ;;
    NO-PULL-REQUEST) return 0 ;;
    *) log_changed "signals-$phase" "pull request signals for phase $phase were not read (see $out)"; return 0 ;;
  esac
  summary="$(grep -o '"kind": *"[a-z_]*"' "$out" | sed 's/.*"\([a-z_]*\)"$/\1/' | sort | uniq -c | awk '{printf "%s%s %s", (NR > 1 ? ", " : ""), $1, $2}' || true)"
  [ -n "$summary" ] || return 0
  log "pull request signals for phase $phase: $summary"
}
