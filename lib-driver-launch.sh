#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Build the launch environment and open the phase window.
#
_drive_launch() {
  local envs=""
  # The stage-title hook (pre_step) needs the session and the phase number; glide
  # passes neither, so both travel through the launch environment. The child
  # driver loads the same configuration file, named on its own command line.
  envs="$envs CLIO_TMUX_SESSION='$SESSION' CLIO_PHASE_NUMBER='$number'"
  [ -z "${DISABLE_NOTIFY:-}" ] || envs="$envs DISABLE_NOTIFY='$DISABLE_NOTIFY'"
  [ -z "${DISABLE_DISCORD:-}" ] || envs="$envs DISABLE_DISCORD='$DISABLE_DISCORD'"
  [ -z "${DRIVER_STUB_RC:-}" ] || envs="$envs DRIVER_STUB_RC='$DRIVER_STUB_RC'"
  [ -z "${DRIVER_STUB_SLEEP:-}" ] || envs="$envs DRIVER_STUB_SLEEP='$DRIVER_STUB_SLEEP'"
  [ -z "${DRIVER_STUB_WAIT_FILE:-}" ] || envs="$envs DRIVER_STUB_WAIT_FILE='$DRIVER_STUB_WAIT_FILE'"
  [ -z "${DRIVER_STUB_READY_FILE:-}" ] || envs="$envs DRIVER_STUB_READY_FILE='$DRIVER_STUB_READY_FILE'"
  [ -z "$reservation_id" ] || envs="$envs CLIO_RESERVATION_ID='$reservation_id'"
  [ -z "$reservation_generation" ] || envs="$envs CLIO_RESERVATION_GENERATION='$reservation_generation'"
  local self_quoted number_quoted rel_quoted config_arg="" quoted setting
  printf -v self_quoted '%q' "$SELF"
  printf -v number_quoted '%q' "$number"
  printf -v rel_quoted '%q' "$rel"
  for setting in "${ARGUS_CONFIG_ARGS[@]}"; do
    printf -v quoted '%q' "$setting"
    config_arg="$config_arg $quoted"
  done
  local launch="${envs:+$envs }bash $self_quoted$config_arg --session $number_quoted $rel_quoted"

  # Geometry: the session is created once at TMUX_WIDTH x TMUX_HEIGHT with
  # `window-size manual`, so it holds that size even when a smaller display
  # attaches. runner.py copies the pane size into each harness pty, so this is
  # what the agent actually works at. The phase window is then sized explicitly,
  # because new-window has no -x/-y and inherits the session size instead.
  #
  # The session is ensured first and never killed, so a display attached to the
  # idle window survives the launch. 9>&- drops the flock fd, as before.
  9>&- tmux_session_ensure \
    || { notify "phase $number failed to create tmux session '$SESSION'"; { _DRIVE_RC=1; return 0; }; }
  # Set the title BEFORE the window opens. The child sets its own titles as it
  # runs, and anything written after the window opens races with them: a parent
  # "starting" landing after the child's "done" would leave the display showing
  # a phase that already finished.
  tmux_set_title "phase $number starting"
  9>&- tmux_phase_window_open "$launch" "$RUN_DIR/session-$number.log" \
    || { notify "phase $number failed to launch in tmux"; { _DRIVE_RC=1; return 0; }; }
  printf '%s\n' "$number" >"$CURRENT"
  # The claim fence was written by the tick, with the claim itself, so a refusal
  # between the two cannot strand the reservation.
  # The array this run covers. Written on every launch, stubbed or not, so a
  # later tick's release log can say which group went back and a reader can see
  # what the run was for. A launch outside a group clears it.
  group_record
  # The window stays named 'phase'. Renaming it to carry the phase number would
  # break every target string in phase-tmux.sh that looks it up by name, and
  # the phase number is already in the title and the log. The stage hook
  # renames it per stage instead, which is the display-facing label.
  tmux_phase_window_select
  log "launched phase $number in tmux '$SESSION' window 'phase'"
  return 0
}
