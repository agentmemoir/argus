#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Release: give the current phase group back without halting the line, and
# the phase-window wait shared by stop and release.
#
# Sourced by phase-driver.sh; not executable on its own.
#
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
  if [ -f "$CLAIM" ]; then
    IFS=$'\t' read -r claim_number reservation_id generation <"$CLAIM" || true
    [ -n "$claim_number" ] && number="${number:-$claim_number}"
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
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    --mode checkpoint >"$RUN_DIR/release-checkpoint.json" 2>/dev/null \
    || log "release: could not push the work in progress; the next owner starts from what is already on the branch (see $RUN_DIR/release-checkpoint.json)"
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
      issue_announce "$number" released
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
