#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The operator commands that end a run: stop, release, and the window wait they
# share.
#
# These live beside the tick rather than inside it so the tick file stays about
# selecting, branching, and launching. `stop` writes the halt marker and holds
# the claim for an explicit `--resume`; `release` gives the group back without
# halting; both drop the driver lock (fd 9) on every return path, so a caller
# that runs a second driver command in the same shell is not refused by a lock
# this process still holds.

stop() {
  mkdir -p "$RUN_DIR"
  # The halt marker is this machine's own run state and names no board
  # reservation, so it is written on every path: no claim, a bad claim, and a
  # board that cannot answer must all still stop the line. Only the
  # `stopped-claim` hold names a reservation, so only it waits on the board.
  driver_lock_wait || log "stop: the driver lock is held by a running tick; stopping anyway"
  printf 'stopped by operator at %s\n' "$(ts)" >"$HALT"
  local stopped_phase=""
  if ! claim_read_fields "$CLAIM" || [ -z "$CLAIM_NUMBER" ] \
      || [ -z "$CLAIM_ID" ] || [ -z "$CLAIM_GEN" ]; then
    log "stop: no readable claim fence; wrote the halt marker and recorded no stopped claim (see $CLAIM)"
  # The board is the authority for the hold: a claim file alone proves
  # nothing, because an expired, released, or taken-over reservation still
  # reads live to the machine that held it once.
  # A stop must not queue behind a running board fetch. When the checkout's fetch
  # lock is busy, the check refuses at once and the stop records no stopped claim.
  elif ! board_assert_owner "$CLAIM_NUMBER" "$CLAIM_ID" "$CLAIM_GEN" nowait; then
    log "stop: the board does not confirm the fence for ${work_kind:-phase} $CLAIM_NUMBER; wrote the halt marker and recorded no stopped claim (see $RUN_DIR/assert-owner-$CLAIM_NUMBER.json)"
  else
    stopped_phase="$CLAIM_NUMBER"
    # The live window names the phase actually running; the claim file can be
    # a stale record from an earlier run. Prefer the live phase when it is a
    # number, so a resume targets what was stopped and not what was recorded.
    local live_phase
    live_phase="$(cat "$CURRENT" 2>/dev/null || true)"
    case "$live_phase" in
      ""|*[!0-9]*) ;;
      *) stopped_phase="$live_phase" ;;
    esac
  fi
  [ -z "$stopped_phase" ] || printf '%s\n' "$stopped_phase" >"$RUN_DIR/stopped-claim"
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
  # Drop the lock this function may have taken. A caller that runs another driver
  # command in the same shell would otherwise inherit the held fd and be refused.
  # No redirection on this line, and that is load-bearing: on an `exec` command a
  # redirection applies to the shell and persists, so hiding a bad-fd message
  # here would send this shell's stderr to /dev/null for the rest of the run.
  exec 9>&- || true
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
  # Best-effort serialize with a tick's own claim and hold reads. A release must
  # still return the group when the lock is busy.
  driver_lock_wait || log "release: the driver lock is held by a running tick; releasing without it"
  local number="" claim_number="" reservation_id="" generation="" rc=0
  number="$(cat "$CURRENT" 2>/dev/null || true)"
  # The claim is parsed by hand (claim_read_fields) because a raw IFS read
  # collapses empty fields. Missing, unreadable, incomplete, or phase-mismatched
  # claims prove no ownership, so release refuses before it interrupts, pushes,
  # or clears local state.
  if ! claim_read_fields "$CLAIM"; then
    log "release: no readable claim fence; refusing to release (see $CLAIM)"
    exec 9>&- || true
    return 1
  fi
  claim_number="$CLAIM_NUMBER"
  reservation_id="$CLAIM_ID"
  generation="$CLAIM_GEN"
  if [ -z "$claim_number" ] || [ -z "$reservation_id" ] || [ -z "$generation" ]; then
    log "release: the claim fence is incomplete; refusing to release (see $CLAIM)"
    exec 9>&- || true
    return 1
  fi
  number="${number:-$claim_number}"
  if [ "$number" != "$claim_number" ]; then
    log "release: current phase $number does not match claim phase $claim_number; refusing to release"
    exec 9>&- || true
    return 1
  fi
  # The board is the authority for who may push. A stale or foreign fence must
  # not checkpoint another owner's branch, so the fence is proved before the
  # first write, and an unconfirmed claim fails closed: nothing is interrupted,
  # nothing is pushed, and the group is not released.
  if ! board_assert_owner "$number" "$reservation_id" "$generation"; then
    log "release: the board does not confirm the fence for ${work_kind:-phase} $number; no checkpoint, no release (see $RUN_DIR/assert-owner-$number.json)"
    # Drop the lock this function may have taken; see the note in stop(). A
    # refusal is a finished run for this process, and a shell that kept fd 9
    # here refused the operator's next driver command.
    exec 9>&- || true
    return 1
  fi
  # Checkpoint and clean must read the fence this command just proved, including
  # claims from older runs that have no saved reservation yet.
  if [ "${work_kind:-phase}" = phase ] \
      && ! record_phase_reservation "$number" "$reservation_id" "$generation"; then
    log "release: could not save the proved phase fence; no checkpoint, no release"
    exec 9>&- || true
    return 1
  fi
  # Written before the interrupt: the child's signal trap reads it and stops
  # without writing a halt marker. The parent removes it on every path.
  : >"$RELEASE"
  if tmux_phase_window_alive; then
    tmux_phase_window_interrupt
    log "release: sent Ctrl-C to '$SESSION:phase'${number:+ for ${work_kind:-phase} $number}"
  else
    log "release: no live phase window${number:+ for ${work_kind:-phase} $number}"
  fi
  wait_phase_window_gone
  # Partial work first, so the next owner resumes instead of starting over. A
  # failure is logged, never a halt. gitsync_clean lands the private run state;
  # the checkpoint lands the public checkout's unfinished work on the branch the
  # next owner resumes from, which is the handoff the requirement promises.
  local checkpoint_args=(--mode checkpoint)
  # The phase is named so the checkpoint re-proves the same fence the release
  # just read, immediately before it stages and pushes. The handoff is the one
  # write that must never land under a claim this machine no longer holds.
  [ -z "$number" ] || checkpoint_args+=(--phase "$number")
  if [ "${work_kind:-phase}" = issue ]; then
    # An issue's edits that cannot be saved halt the line; the claim is kept.
    if ! issue_stop_save "$number"; then
      exec 9>&- || true
      return 1
    fi
  else
    "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
      "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
      "${checkpoint_args[@]}" >"$RUN_DIR/release-checkpoint.json" 2>/dev/null \
      || log "release: could not push the work in progress; the next owner starts from what is already on the branch (see $RUN_DIR/release-checkpoint.json)"
  fi
  [ -z "$number" ] || gitsync_clean "$number"
  # The operator asked to release, so the local manual-stop hold goes whatever
  # the board write does; clearing it only on success left an expired release
  # holding the line on a hold nothing would consume.
  rm -f "$RUN_DIR/stopped-claim"
  # The array the run covered, for the log line below. Read before the board
  # write, because the write is what ends the group on this machine.
  local group_members=""
  [ -s "$GROUPFILE" ] && group_members="$(tr '\n' ' ' <"$GROUPFILE")"
  if [ -n "$reservation_id" ] && [ -n "$generation" ]; then
    if "$PYTHON" "$ARGUS/board/reservations.py" release-group \
        "${ARGUS_CONFIG_ARGS[@]}" "${BOARD_KIND_ARGS[@]+"${BOARD_KIND_ARGS[@]}"}" --repo-root "$REPO" \
        --machine-id "$PHASE_MACHINE_ID" \
        --reservation-id "$reservation_id" \
        --generation "$generation" \
        >"$RUN_DIR/release${number:+-$number}.json" 2>&1; then
      if [ -n "$group_members" ]; then
        log "release: returned the group for ${work_kind:-phase} $number to the queue [$group_members]"
      else
        log "release: returned the group for ${work_kind:-phase} $number to the queue"
      fi
      rm -f "$CLAIM"
    else
      rc=$?
      log "release: could not return the group for ${work_kind:-phase} $number (rc=$rc; see $RUN_DIR/release${number:+-$number}.json)"
    fi
  else
    log "release: no board token was recorded; the run was stopped without a board write"
  fi
  rm -f "$CURRENT" "$PIDFILE" "$RELEASE" "$GROUPFILE" "$GROUPBRANCH"
  # Drop the lock this function may have taken; see the note in stop().
  exec 9>&- || true
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
