#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The tick: reserve, branch, and launch a phase.
#
_drive() {
  cd "$REPO"

  _DRIVE_RC=""
  if [ "$DRY_RUN" = 1 ]; then
    _drive_dry_run
    return "${_DRIVE_RC:-0}"
  fi

  mkdir -p "$RUN_DIR"

  if [ -f "$HALT" ]; then
    log "halted ($(tr '\n' ' ' <"$HALT")); refusing to advance"
    return 0
  fi

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK"
    flock -n 9 || { log "another driver instance holds the lock; skip"; return 0; }
  fi

  if tmux_busy; then
    check_stall
    # The tick loop is the renewer of the group this run belongs to. The board
    # measures a running group from its newest heartbeat, so a claim nobody
    # renews expires under a live run and another machine may take the group
    # away from a run that is still working on it.
    group_renew
    log "phase window is running; skip"
    return 0
  fi

  # No phase window, but a recorded live PID means an orphaned run (e.g. after
  # `tmux kill-window`, which does not signal the pane). The session itself
  # outlives every phase, so it proves nothing. Halt rather than start a
  # second, concurrent run.
  if [ -f "$PIDFILE" ]; then
    local opid
    opid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$opid" ] && kill -0 "$opid" 2>/dev/null; then
      log "orphaned run detected (pid $opid, no phase window); halting"
      [ -f "$HALT" ] || printf 'orphaned run pid %s at %s\n' "$opid" "$(ts)" >"$HALT"
      notify "a phase run (pid $opid) is alive but its tmux phase window is gone (orphaned). Line stopped; investigate."
      return 0
    fi
    rm -f "$PIDFILE"
  fi

  # No live phase: clear per-session state a hard kill may have left behind.
  rm -f "$CURRENT" "$STALL"
  prune
  # Keep the display truthful between phases. A halted line says so here too, so
  # the reason is visible even if the titlebar is not rendered.
  if [ -f "$HALT" ]; then
    tmux_set_title "HALTED - $(tr '\n' ' ' <"$HALT" | cut -c1-60)"
  else
    tmux_set_title "idle - waiting for next phase"
  fi


  # The fence is empty until a path fills it in. Every branch that reaches the
  # launch must set both, because `set -u` makes a missing one fatal and an
  # empty one silently drops the child's ownership fence.
  local out rc=0 number rel reservation_id="" reservation_generation=""
  local selection selection_rc=0 selection_state unknown_state
  # Everything that can refuse a launch because of how this machine is
  # configured is asked here, above the selector. Below this point the tick holds
  # a claim on the shared board, and a claim is not something the line can give
  # back on its own. The selector refuses the same two things, but as an empty
  # result with exit 2, which this script reads as "nothing to do"; without these
  # checks a typo in a settings file looks exactly like a queue that has run dry,
  # and a folder outside the checkout looks like a phase that launched and died.
  if ! roadmap_preflight; then
    log "$PREFLIGHT_REASON; refusing to launch"
    return 1
  fi
  _drive_choose_selection "${1:-}" || return $?
  [ -z "$_DRIVE_RC" ] || return "$_DRIVE_RC"

  log "next phase: $number ($rel)"

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
    local roadmap_name
    roadmap_name="$(basename "$rel" .md)"
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
    tidy_published_checkout || { _DRIVE_RC=1; return 0; }
    # The exit code is captured by running the command and reading $? straight
    # after. Putting it behind `if ! cmd; then rc=$?` cannot work: the `!`
    # resets $? to 0 before the body runs, so the guard below saw success even
    # when the sync refused, and the phase launched anyway.
    # --roadmap-dir as well as --roadmap: the first names the phase file's
    # behaviour, and the second names the folder that file was chosen from.
    # gitsync's branch mode scans that folder to refuse a branch name another
    # phase in it would also earn, so without it the check runs against the
    # derived folder and a collision inside a configured one goes unseen.
    #
    # A group passes its members as well, so the one branch the whole group
    # publishes is what the helper cuts and what the collision scan checks. The
    # name is derived from the group's lowest member, so every tick of the group
    # asks for the same branch whichever member it is launching.
    "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      --mode branch --phase "$number" --roadmap "$roadmap_name" \
      --roadmap-dir "$(roadmap_dir)" \
      "${GROUP_ARGS[@]+"${GROUP_ARGS[@]}"}" \
      >"$RUN_DIR/phase-branch.json" 2>/dev/null
    local branch_rc=$?
    if [ "$branch_rc" -ne 0 ]; then
      log "could not put phase $number on its own branch; refusing to launch (see $RUN_DIR/phase-branch.json)"
      { _DRIVE_RC=1; return 0; }
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
    log "phase $number: $(branch_state_line "$RUN_DIR/phase-branch.json")"
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
    "$PYTHON" "$ARGUS/gitsync.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode resync \
      >"$RUN_DIR/phase-resync.json" 2>/dev/null
    local resync_rc=$?
    log "phase $number: $(resync_state_line "$RUN_DIR/phase-resync.json")"
    if [ "$resync_rc" -ne 0 ]; then
      log "the integration branch did not merge cleanly into phase $number; the phase agent finishes it (see $RUN_DIR/phase-resync.json)"
    fi
  fi
  return 0
}

drive() {
  local rc=0
  mkdir -p "$RUN_DIR"
  log "configuration: driver=$DRIVER repo=$REPO roadmap=$(roadmap_dir) config=${ARGUS_CONFIG_FILE:-none}; $CONFIG_PRECEDENCE"
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

stop() {
  mkdir -p "$RUN_DIR"
  printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"
  # Ctrl-C must reach the phase window. Targeting the session would send it to
  # whichever window is current, which may be the idle status display.
  if tmux_phase_window_alive; then
    tmux_phase_window_interrupt
    log "stop: sent Ctrl-C to '$SESSION:phase' and wrote halt marker"
  elif tmux_session_exists; then
    log "stop: wrote halt marker (session idle, no phase window)"
  else
    log "stop: wrote halt marker (no live session)"
  fi
}

# Give the current phase's group back without halting the line.
#
# This is deliberately not `stop()`. stop() writes the halt marker before it
# does anything else; a release is the opposite. Nothing here writes the halt
# marker, triggers the orphan or stall check, or sends a halt notification. It
# interrupts only the phase window, waits for that window to close, clears the
# same run-state files the signal path clears, and returns 0. The board group is
# released after partial work is pushed, so the next owner resumes from the
# branch. A failure to push or to release is logged and never halts.
release() {
  mkdir -p "$RUN_DIR"
  local number="" claim_number="" reservation_id="" generation="" rc=0
  number="$(cat "$CURRENT" 2>/dev/null || true)"
  if [ -f "$CLAIM" ]; then
    IFS=$'\t' read -r claim_number reservation_id generation <"$CLAIM" || true
    [ -n "$claim_number" ] && number="${number:-$claim_number}"
  fi
  # Written before the interrupt: the child's signal trap reads it and stops
  # without writing a halt marker. The parent removes it on every path.
  : >"$RELEASE"
  if tmux_phase_window_alive; then
    tmux_phase_window_interrupt
    log "release: sent Ctrl-C to '$SESSION:phase'${number:+ for phase $number}"
  else
    log "release: no live phase window${number:+ for phase $number}"
  fi
  wait_phase_window_gone
  # Partial work first, so the next owner resumes instead of starting over. A
  # failure is logged, never a halt.
  [ -z "$number" ] || gitsync_clean "$number"
  # The array the run covered, for the log line below. Read before the board
  # write, because the write is what ends the group on this machine.
  local group_members=""
  [ -s "$GROUPFILE" ] && group_members="$(tr '\n' ' ' <"$GROUPFILE")"
  if [ -n "$reservation_id" ] && [ -n "$generation" ]; then
    if "$PYTHON" "$ARGUS/phase_reservations.py" release-group \
        "${ARGUS_CONFIG_ARGS[@]}" --repo-root "$REPO" \
        --machine-id "$PHASE_MACHINE_ID" \
        --reservation-id "$reservation_id" \
        --generation "$generation" \
        >"$RUN_DIR/release${number:+-$number}.json" 2>&1; then
      if [ -n "$group_members" ]; then
        log "release: returned the group for phase $number to the queue [$group_members]"
      else
        log "release: returned the group for phase $number to the queue"
      fi
      rm -f "$CLAIM"
    else
      rc=$?
      log "release: could not return the group for phase $number (rc=$rc; see $RUN_DIR/release${number:+-$number}.json)"
    fi
  else
    log "release: no board token was recorded; the run was stopped without a board write"
  fi
  rm -f "$CURRENT" "$PIDFILE" "$RELEASE" "$GROUPFILE" "$GROUPBRANCH"
  return 0
}

# Wait for the phase window to disappear. The session itself is persistent and
# is never waited on.
wait_phase_window_gone() {
  local i=0
  while tmux_window_exists; do
    i=$((i + 1)); [ "$i" -gt 60 ] && break
    sleep 0.25
  done
  sleep 0.3
}
