#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
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
  [ -z "${DRIVER_STUB_RC:-}" ] || envs="$envs DRIVER_STUB_RC='$DRIVER_STUB_RC'"
  [ -z "${DRIVER_STUB_SLEEP:-}" ] || envs="$envs DRIVER_STUB_SLEEP='$DRIVER_STUB_SLEEP'"
  [ -z "${DRIVER_STUB_WAIT_FILE:-}" ] || envs="$envs DRIVER_STUB_WAIT_FILE='$DRIVER_STUB_WAIT_FILE'"
  [ -z "${DRIVER_STUB_READY_FILE:-}" ] || envs="$envs DRIVER_STUB_READY_FILE='$DRIVER_STUB_READY_FILE'"
  # The child retries a failed job; it reads the count from its own environment,
  # and a pane takes the tmux server's environment rather than this shell's, so
  # the value travels with the launch like the stub hooks do.
  [ -z "${ARGUS_JOB_RETRIES:-}" ] || envs="$envs ARGUS_JOB_RETRIES='$ARGUS_JOB_RETRIES'"
  [ -z "$reservation_id" ] || envs="$envs CLIO_RESERVATION_ID='$reservation_id'"
  [ -z "$reservation_generation" ] || envs="$envs CLIO_RESERVATION_GENERATION='$reservation_generation'"
  # A resume tells the child not to archive the run it is continuing; the child
  # re-checks the resume gate itself, because the state can move between here and
  # the launch.
  [ "${RESUME_MODE:-0}" != 1 ] || envs="$envs CLIO_RESUME='1'"
  local self_quoted number_quoted rel_quoted config_arg="" quoted setting
  if [ "${work_kind:-phase}" = issue ]; then
    printf -v quoted '%q' "$ARGUS_CONFIG_FILE"
    envs="$envs ARGUS_CONFIG_FILE=$quoted"
    printf -v quoted '%q' "$ISSUES_REPO"
    envs="$envs ARGUS_ISSUES_REPO=$quoted ARGUS_WORK_KIND=issue"
  fi
  printf -v self_quoted '%q' "$SELF"
  printf -v number_quoted '%q' "$number"
  printf -v rel_quoted '%q' "$rel"
  for setting in "${ARGUS_CONFIG_ARGS[@]}"; do
    printf -v quoted '%q' "$setting"
    config_arg="$config_arg $quoted"
  done
  local launch="${envs:+$envs }bash $self_quoted$config_arg --session $number_quoted $rel_quoted"

  # Geometry: no window has a fixed size. The phase window follows the attached
  # client (`window-size latest`), so the agent works at the size of the screen
  # it is shown on. TMUX_WIDTH x TMUX_HEIGHT is only the size used when no client
  # is attached.
  #
  # The session is ensured first and never killed, so a display attached to the
  # idle window survives the launch. 9>&- drops the flock fd, as before.
  9>&- tmux_session_ensure \
    || { notify "${work_kind:-phase} $number failed to create tmux session '$SESSION'"; { _DRIVE_RC=1; return 0; }; }
  # Set the title BEFORE the window opens. The child sets its own titles as it
  # runs, and anything written after the window opens races with them: a parent
  # "starting" landing after the child's "done" would leave the display showing
  # a phase that already finished.
  tmux_set_title "${work_kind:-phase} $number starting"
  9>&- tmux_phase_window_open "$launch" "$RUN_DIR/session-$number.log" \
    || { notify "${work_kind:-phase} $number failed to launch in tmux"; { _DRIVE_RC=1; return 0; }; }
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
  log "launched ${work_kind:-phase} $number in tmux '$SESSION' window 'phase'"
  return 0
}
