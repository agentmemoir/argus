#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The tick: reserve, branch, and launch a phase.
#
# Two prologue steps are shared with `--resume`, which enters the same launch
# path without running a tick. They live here rather than inline so the two
# callers cannot drift apart.

# Take the driver lock, or report that another instance holds it.
driver_lock_take() {
  mkdir -p "$RUN_DIR"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }
  fi
  return 0
}

# Take the driver lock, waiting briefly for a tick that is finishing. The wait is
# bounded, and the caller proceeds without the lock when it is not won: a stop
# must still write its halt marker, and a release must still return the group.
driver_lock_wait() {   # [tries]
  command -v flock >/dev/null 2>&1 || return 0
  mkdir -p "$RUN_DIR"
  exec 9>"$LOCK"
  local tries="${1:-10}" i=0
  while [ "$i" -lt "$tries" ]; do
    flock -n 9 && return 0
    i=$((i + 1)); sleep 0.1
  done
  return 1
}

# Halt when a recorded live PID has no phase window: an orphaned run.
#
# `tmux kill-window` does not signal the pane, so the pid outlives the window and
# a second run would race it. A dead pid is removed and the caller proceeds.
driver_orphan_check() {
  [ -f "$PIDFILE" ] || return 0
  local opid
  opid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$opid" ] && kill -0 "$opid" 2>/dev/null; then
    log "orphaned run detected (pid $opid, no phase window); halting"
    [ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
    notify "a phase run (pid $opid) is alive but its tmux phase window is gone (orphaned). Line stopped; investigate."
    return 1
  fi
  rm -f "$PIDFILE"
  return 0
}

# True when the manual-stop hold outlived the board's heartbeat window, so the
# claim it named has expired and this machine no longer owns the work. The check
# rides the hold file's own modification time, written when the stop happened.
stopped_claim_expired() {   # <file>
  "$PYTHON" - "$ARGUS" "$1" <<'PY'
import os
import sys
import time

sys.path.insert(0, sys.argv[1])
import _bootstrap  # noqa: F401 - register the source package
from argus.board.core import GROUP_HEARTBEAT_WINDOW_S

try:
    age = time.time() - os.path.getmtime(sys.argv[2])
except OSError:
    raise SystemExit(1)
raise SystemExit(0 if age > GROUP_HEARTBEAT_WINDOW_S else 1)
PY
}

_drive() {
  cd "$REPO"

  # Every tick owns its launch mode. Without this reset a resume in an earlier
  # tick of the same shell leaves the flag set, and the next ordinary launch
  # tells its child it is continuing a run. On a host with `flock` the tick runs
  # in the caller's shell, so the flag survives between ticks; the portable lock
  # path runs in a subshell and hid the leak.
  RESUME_MODE=0

  _DRIVE_RC=""
  if [ "$DRY_RUN" = 1 ]; then
    _drive_dry_run
    return "${_DRIVE_RC:-0}"
  fi

  mkdir -p "$RUN_DIR"

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    flock -n 9 || { log "another driver instance holds the lock; skip"; return 1; }
  fi

  # The tick is the renewer of the claim this machine holds. The board measures a
  # claim from its newest heartbeat once work has begun, so a claim nobody renews
  # expires under a live run and another machine may take the work away.
  if tmux_busy; then
    claim_renew
    check_stall
    log "${work_kind:-phase} window is running; skip"
    return 0
  fi

  driver_orphan_check || return 0

  # Publication is maintenance of finished work. It never selects or reserves
  # work, and a halt holds launches while these receipts and group gates settle.
  # A receipt read that fails is a repair; a blocked tidy is a narrow own-state
  # or lost-work stop, so that one return still stops the tick.
  reconcile_pending_reviews || return 1
  launchfault_due
  if [ -f "$HALT" ]; then
    log_changed halted "halted ($(tr '\n' ' ' <"$HALT")); no new work"
    return 0
  fi
  [ "${1:-}" != --merge-check-only ] || return 0
  # A manual stop needs an explicit resume or release. Clearing the line's halt
  # alone must not feed the stopped claim back into recover-own-claim. The hold
  # is not renewed, so it is dropped once its claim's window has passed.
  if [ -s "$RUN_DIR/stopped-claim" ]; then
    if stopped_claim_expired "$RUN_DIR/stopped-claim"; then
      log_changed stopped "the stopped claim's window passed; releasing the hold"
      rm -f "$RUN_DIR/stopped-claim"
    else
      log_changed stopped "a manually stopped claim waits for --resume or --release; no new work"
      return 0
    fi
  fi
  # Renew before any progress return, so a tick that settles a receipt or a group
  # gate still heartbeats the claim of a run this machine is driving. An idle or
  # manually stopped line returns above and does not renew.
  claim_renew
  if [ "$PENDING_PUBLISHED" -gt 0 ]; then
    return 0
  fi

  # No live phase: clear per-session state a hard kill may have left behind.
  rm -f "$CURRENT" "$STALL"
  prune
  # Keep the display truthful between phases. A halted line says so here too, so
  # the reason is visible even if the titlebar is not rendered.
  if [ -f "$HALT" ]; then
    tmux_set_title "HALTED - $(tr '\n' ' ' <"$HALT" | cut -c1-60)"
  else
    tmux_set_title "idle - waiting for next ${work_kind:-phase}"
  fi


  # The fence is empty until a path fills it in. Every branch that reaches the
  # launch must set both, because `set -u` makes a missing one fatal and an empty
  # one silently drops the child's ownership fence; a resume fills them too.
  local out rc=0 number rel reservation_id="" reservation_generation=""
  local selection selection_rc=0 selection_state unknown_state

  # A cleared marker with a record beside it is a resume; `_drive_resume` runs it.
  if [ ! -f "$HALT" ] && [ -f "$HALT_RECORD" ]; then
    RESUME_FROM_TICK=1
    _drive_resume
    return $?
  fi

  # Everything that can refuse a launch because of how this machine is
  # configured is asked here, above the selector. Below this point the tick holds
  # a claim on the shared board, and a claim is not something the line can give
  # back on its own. The selector refuses the same two things, but as an empty
  # result with exit 2, which this script reads as "nothing to do"; without these
  # checks a typo in a settings file looks exactly like a queue that has run dry,
  # and a folder outside the checkout looks like a phase that launched and died.
  if ! work_preflight; then
    log "$PREFLIGHT_REASON; refusing to launch"
    return 1
  fi
  _drive_choose_selection "${1:-}" || return $?
  [ -z "$_DRIVE_RC" ] || return "$_DRIVE_RC"

  log "next ${work_kind:-phase}: $number ($rel)"

  # The fence goes down with the claim, not at the launch. A tick that refuses
  # after this point, at the branch cut or the sync gate, still leaves the claim
  # releasable by `--release` instead of stranded on the board until its window
  # closes. A claim the board does not confirm is a refused fence: the tick
  # stops here, before the branch cut and the launch, because a launch would
  # run without an ownership fence.
  #
  # Both shapes of that fence are written, because the tick's own fenced steps
  # read the file and only this shell reads the claim; see record_claim_fence.
  record_claim_fence "$number" "$reservation_id" "$reservation_generation" || {
    rc=$?; release_refused_claim "$number" "$reservation_id" "$reservation_generation"; return "$rc"
  }

  _drive_cut_branch || return $?
  [ -z "$_DRIVE_RC" ] || return "$_DRIVE_RC"

  _drive_launch || return $?
  [ -z "$_DRIVE_RC" ] || return "$_DRIVE_RC"
  return 0
}

_drive_cut_branch() {
  # Before the checkout is cut over, land the previous phase's closing run-state
  # writes. A run that ended without its own clean leaves them dirty, and the
  # publish at the end of this phase would refuse on them.
  gitsync_land_leftovers

  # The public checkout is put on the phase's own branch before the launch, from
  # the integration branch the sync just brought up to date, and it carries the
  # phase's staged work across untouched. A failure here is fail-closed: a
  # launch onto the integration branch would put unreviewed product code where
  # other developers can pull it.
  #
  # The private checkout is deliberately left on the integration branch. It
  # holds run state and phase notes, which the operator reads on master, and a
  # phase that waits for a human to merge it stops the line for no gain. The
  # helper reports that as stays-on-integration-branch, so the two checkouts stay
  # distinguishable in the phase's own record.
  if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    local phases_name
    phases_name="$(basename "$rel" .md)"
    # Return the checkout to the integration branch when the phase it is on is
    # already recorded as published. Its own step, on its own tick, because the
    # merge check writes the receipt before it moves the branch: when the move
    # fails, the receipt says published, the selector stops reporting the phase
    # as awaiting review, and nothing would ever retry it. That left the
    # checkout on a dead branch and every later phase refusing to launch on it.
    # The tidy step reads the receipt and finishes the job, so the two outcomes
    # can succeed and fail independently.
    #
    # This is the same call _drive() now makes before the sync gate, and it is
    # kept here because this is the point where the phase branch is about to be
    # cut: a published phase's branch must not still be the one checked out when
    # the next phase starts from it. Running it in both places is deliberate —
    # the step is idempotent, and each place covers a case the other does not.
    # A tidy that cannot preserve its work is the narrow lost-work stop. A
    # preserved, degraded, or clean tidy returns 0 and the launch continues.
    tidy_published_checkout "$number" || { _DRIVE_RC=1; return 0; }
    # The exit code is captured by running the command and reading $? straight
    # after. Putting it behind `if ! cmd; then rc=$?` cannot work: the `!`
    # resets $? to 0 before the body runs, so the guard below saw success even
    # when the sync refused, and the phase launched anyway.
    # --phases-directory as well as --phases: the first names the phase file's
    # behaviour, and the second names the folder that file was chosen from.
    # gitsync's branch mode scans that folder to refuse a branch name another
    # phase in it would also earn, so without it the check runs against the
    # derived folder and a collision inside a configured one goes unseen.
    #
    # A group passes its members as well, so the one branch the whole group
    # publishes is what the helper cuts and what the collision scan checks. The
    # name is derived from the group's lowest member, so every tick of the group
    # asks for the same branch whichever member it is launching.
    "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      --mode branch --phase "$number" --phases "$phases_name" \
      "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
      --phases-directory "$(phases_dir)" \
      "${GROUP_ARGS[@]+"${GROUP_ARGS[@]}"}" \
      >"$RUN_DIR/phase-branch.json" 2>/dev/null
    local branch_rc=$? branch_state branch_reason
    if [ "$branch_rc" -ne 0 ]; then
      # A fence refusal is not a branch failure. The board does not confirm
      # this machine owns the phase, so a launch would run another owner's
      # work. The claim file stays, so --release can still return it.
      branch_state="$(selection_value state <"$RUN_DIR/phase-branch.json")"
      case "$branch_state" in
        FENCED|COORDINATION_UNAVAILABLE)
          log "${work_kind:-phase} $number: the $branch_state fence refused the branch cut; no launch (see $RUN_DIR/phase-branch.json)"
          _DRIVE_RC=1
          return 0
          ;;
      esac
      # An issue has no branch of its own to fall back to: the checkout is on
      # the base branch, so a launch would put issue work there (belief 4).
      # Give the claim back, say why, and launch nothing.
      if [ "${work_kind:-phase}" = issue ]; then
        branch_reason="$(selection_value error <"$RUN_DIR/phase-branch.json")"
        log "issue $number: could not put the issue on its own branch; no launch, nothing ran on the base branch (see $RUN_DIR/phase-branch.json)"
        ISSUE_CLAIM_HELD=1; CLAIM_RELEASED=0
        release_refused_claim "$number" "${reservation_id:-}" "${reservation_generation:-}" failed
        [ "$CLAIM_RELEASED" != 1 ] || ISSUE_CLAIM_HELD=0
        notify_phase "$rel" branch-refused "${branch_reason:-${branch_state:-unknown}}"
        _DRIVE_RC=1
        return 0
      fi
      # A phase branch cut that fails is a repair: launch on the branch the
      # checkout already has and retry the cut next tick. The phase branch is published
      # by the finalize push, which reports a real failure there.
      log "could not put ${work_kind:-phase} $number on its own branch; continuing on $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'the current branch'); the next tick retries (see $RUN_DIR/phase-branch.json)"
    fi
    # Only a group records a branch of its own. A single phase's branch is not a
    # group branch, and writing it here would leave a group's own record naming
    # a branch no group ever had.
    if group_active; then
      group_record_branch "$(selection_value branch <"$RUN_DIR/phase-branch.json")"
    fi
    # The two branches are read per checkout by role, from the action each one
    # reported. The receipt's top-level "branch" is the name that was asked
    # for, which is the same string for both checkouts, so printing it as
    # though it were the public checkout's branch reported a phase branch for a
    # checkout that was still sitting on master.
    log "${work_kind:-phase} $number: $(branch_state_line "$RUN_DIR/phase-branch.json")"
    if group_active; then
      log "group $number: one branch $(group_branch_of) covers [$(printf '%s' "$GROUP_MEMBERS" | tr '\n' ' ')]"
    fi
    # The branch may have been cut from an open predecessor rather than from the
    # integration branch, and the integration branch may have moved since the
    # launch-time sync. Pull it in now so the phase starts from a base that
    # contains both. A conflict here is not fatal and is not this script's to
    # resolve: the helper leaves it in place and names the paths, and the phase
    # agent finishes it. Refusing to launch would put the human back on the
    # critical path for a conflict the agent is there to fix.
    "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode resync \
      >"$RUN_DIR/phase-resync.json" 2>/dev/null
    local resync_rc=$?
    log "${work_kind:-phase} $number: $(resync_state_line "$RUN_DIR/phase-resync.json")"
    if [ "$resync_rc" -ne 0 ]; then
      log "the integration branch did not merge cleanly into ${work_kind:-phase} $number; the phase agent finishes it (see $RUN_DIR/phase-resync.json)"
    fi
  fi
  return 0
}

drive() {
  local rc=0
  mkdir -p "$RUN_DIR"
  log_changed configuration "configuration: driver=$DRIVER repo=$REPO phases=$(phases_dir) config=${ARGUS_CONFIG_FILE:-none}; $CONFIG_PRECEDENCE"
  if command -v flock >/dev/null 2>&1; then
    _drive "${1:-}" || rc=$?
  else
    mkdir -p "$RUN_DIR"
    # Named without `local`, and released explicitly below as well as from the
    # trap, because a name scoped to this function is already unset by the time a
    # subshell's EXIT trap runs on bash older than 4.4 when this function was
    # called inside a command substitution. The trap then removed nothing, the
    # directory stayed, and every later tick in that process found the lock held
    # and skipped while reporting success.
    PORTABLE_LOCK="$RUN_DIR/driver.lock.d"
    if ! mkdir "$PORTABLE_LOCK" 2>/dev/null; then
      log "another driver instance holds the portable lock; skip"
      heartbeat
      return 0
    fi
    (
      # EXIT only. A trap on a signal without an exit in it would take the lock
      # away while this tick was still running, which is the one thing the lock is
      # for. A tick killed outright therefore leaves the directory behind, which
      # is a limit of this path and not of the flock path above.
      trap 'rmdir "$PORTABLE_LOCK" 2>/dev/null || true' EXIT
      _drive "${1:-}" || rc=$?
      exit "$rc"
    )
    rc=$?
    # Taken out here rather than left to the trap. The trap is the safety net for
    # a tick killed by a signal, and it cannot be the only thing that releases the
    # lock: a tick that returns normally is the common case, and it must not depend
    # on a trap whose scope the caller can change by calling this function from
    # inside a substitution.
    rmdir "$PORTABLE_LOCK" 2>/dev/null || true
  fi
  heartbeat
  return "$rc"
}
