#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# tmux session and window control for the phase driver.
#
# Sourced by argus/driver/phase-driver.sh. Not executable on its own. Expects the
# driver to have already set: SESSION, TMUX_WIDTH, TMUX_HEIGHT, REPO, RUN_DIR,
# LOG, ARGUS, and to run under `set -euo pipefail`.
#
# The model is one permanent session with two windows:
#
#   idle    created once, shows status. Never exits, so the session persists.
#   phase   created per launch, runs the phase. Closes itself when done.
#
# THE RULE THAT MATTERS: every command below names an explicit window target
# (`$SESSION:phase`, `$SESSION:idle`, or the session itself only for
# session-scoped options). tmux resolves a bare session name to that session's
# *current* window, which becomes ambiguous the moment a second window exists.
# A retargeted `send-keys` is the difference between Ctrl-C reaching a running
# agent and Ctrl-C reaching the log tail.
#
# Geometry: no window has a fixed size. `window-size latest` makes each window
# follow the client that was used most recently, and `aggressive-resize on` keeps
# that rule per window. An attached terminal therefore sets the size of the
# agent's pane. TMUX_WIDTH x TMUX_HEIGHT is only the `default-size`, which is
# used when no client is attached. The harness must not pin its own size:
# runner.py copies the pane size into each harness pty, and tmux sends SIGWINCH
# when that pane is resized.

# The two window names. A window whose command exits closes that window, so the
# phase window disappearing is the completion signal.
: "${TMUX_IDLE_WINDOW:=idle}"
: "${TMUX_PHASE_WINDOW:=phase}"
: "${TMUX_IDLE_SCRIPT:=phase-tmux-idle.sh}"

# The tmux binary comes from the loaded configuration. Every tmux call goes through
# this wrapper. A tmux server keeps each descriptor of the client that starts it, so
# a lock descriptor left open there stays locked for as long as the server lives.
# Fd 9 is the driver's own lock (`exec 9>`). Fd 3 is the lock under cron, where
# `flock -n <lock> <cmd>` opens the lock file on the lowest free descriptor and the
# tick inherits it. Fd 8 is the checkout lock a phase holds for its run (issue 199),
# so a tmux server started from the phase process cannot keep it.
tmux_lockfree() { "$CMD_TMUX" "$@" 3>&- 8>&- 9>&-; }
TMUX_BIN=tmux_lockfree

tmux_session_exists() {
  "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null
}

# A window exists only if the session exists and the window is found by name.
# Matching is exact so a window called "phase-old" never counts as the phase.
tmux_window_exists() {
  "$TMUX_BIN" list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null \
    | grep -qx -- "$TMUX_PHASE_WINDOW"
}

# The phase window's tmux ID (@N), which is stable for the life of the window
# and does not change when it is renamed. Every lookup goes through this, so a
# stage label like "phase adversary" never makes the window unfindable. Prints
# nothing and returns nonzero when there is no phase window.
tmux_window_id() {
  "$TMUX_BIN" list-windows -t "$SESSION" -F '#{window_id} #{window_name}' 2>/dev/null \
    | awk -v want="$TMUX_PHASE_WINDOW" '$2 == want { print $1; found=1 } END { exit !found }'
}

# True when a phase is genuinely running: the window exists and its pane is not
# a dead-but-retained pane. A pane left dead by remain-on-exit must not block the
# line forever, so it counts as not-running.
tmux_phase_window_alive() {
  local wid dead
  wid="$(tmux_window_id)" || return 1
  dead="$("$TMUX_BIN" list-panes -t "$wid" -F '#{pane_dead}' 2>/dev/null | sort -u)"
  [ "$dead" = "1" ] && return 1
  return 0
}

# Create the session and its idle window if they are missing. Never kills the
# session: it is meant to outlive every phase.
tmux_session_ensure() {
  if tmux_session_exists; then
    tmux_session_reconcile
    return 0
  fi
  log "creating tmux session '$SESSION' (persistent)"
  # The idle script must be executable: tmux execs it directly, and a
  # non-executable file makes the window close instantly, which would take the
  # whole session with it.
  #
  # The message names the mount when the mode is correct but the filesystem still
  # refuses. "not executable" on its own describes a permission that may well be
  # right, and a reader sent to fix a chmod on a noexec mount fixes nothing and
  # learns nothing.
  if [ ! -x "$ARGUS_DRIVER/$TMUX_IDLE_SCRIPT" ]; then
    if [ -r "$ARGUS_DRIVER/$TMUX_IDLE_SCRIPT" ]; then
      log "idle script $ARGUS/$TMUX_IDLE_SCRIPT is readable but not executable; the filesystem holding it most likely does not permit running a file (a noexec mount)"
    else
      log "idle script $ARGUS/$TMUX_IDLE_SCRIPT is missing or unreadable"
    fi
    return 1
  fi
  # A new tmux server inherits every open descriptor. 9>&- keeps the driver lock
  # (fd 9) out of it, so the lock is released when the driver exits.
  9>&- "$TMUX_BIN" new-session -d -s "$SESSION" -c "$REPO" \
    -n "$TMUX_IDLE_WINDOW" "$ARGUS_DRIVER/$TMUX_IDLE_SCRIPT" "${ARGUS_CONFIG_ARGS[@]}" \
    || { log "failed to create tmux session '$SESSION'"; return 1; }
  tmux_apply_size_options
  "$TMUX_BIN" set-option -t "$SESSION" set-titles on 2>/dev/null || true
  return 0
}

# Size options: no window has a fixed size. Windows follow the attached client
# (`latest`, per window). The default size applies only when no client is
# attached, so a detached phase window still has room.
tmux_apply_size_options() {
  "$TMUX_BIN" set-option -t "$SESSION" -w window-size latest 2>/dev/null || true
  "$TMUX_BIN" set-option -t "$SESSION" -w aggressive-resize on 2>/dev/null || true
  "$TMUX_BIN" set-option -t "$SESSION" -w default-size "${TMUX_WIDTH}x${TMUX_HEIGHT}" 2>/dev/null || true
}

# Bring an existing session back in line with the current settings.
#
# The session outlives every phase, so it is created once and then reused
# indefinitely. A session created with an older setting, such as a fixed size,
# would otherwise keep it. Re-assert the options on every tick: it is cheap, and
# it makes a setting change take effect without the operator killing the session.
tmux_session_reconcile() {
  tmux_apply_size_options
  "$TMUX_BIN" set-option -t "$SESSION" set-titles on 2>/dev/null || true
  tmux_apply_status_style
  return 0
}

# Status-bar contrast. The tmux default is dim grey text on a green bar, which
# is close to unreadable on a television viewed from across a room.
#
# These are COLOUR options only. Nothing here changes the status-bar row count,
# the window size, or anything written to a pane, so applying it to a session
# with a phase in flight delivers no SIGWINCH and does not disturb the running
# agent. Verified: pane geometry stayed 164x56 and the pane kept producing
# output across the change. Do not add the `status` row-count option here; that
# would resize the window and reach the running agent.
tmux_apply_status_style() {
  # Black on white: the highest contrast ratio available (21:1).
  "$TMUX_BIN" set-option -t "$SESSION" status-style 'fg=colour0,bg=colour15' 2>/dev/null || true
  # The window in use is a solid white block, so which one is live is obvious
  # from a distance. The idle window is black on light grey: still high
  # contrast, but visibly secondary.
  "$TMUX_BIN" set-option -t "$SESSION" window-status-current-style 'fg=colour0,bg=colour15,bold' 2>/dev/null || true
  "$TMUX_BIN" set-option -t "$SESSION" window-status-style 'fg=colour0,bg=colour7' 2>/dev/null || true
}

# Open the phase window and size it. Fails if a phase window already exists,
# which the caller must have ruled out via tmux_phase_window_alive.
tmux_phase_window_open() {
  local launch="$1" logfile="$2" wid
  "$TMUX_BIN" new-window -d -t "$SESSION" -n "$TMUX_PHASE_WINDOW" -c "$REPO" "$launch" || return 1
  # Resolve by ID right away: the window may be renamed by a stage hook moments
  # after this, and every later target must still find it.
  wid="$(tmux_window_id)" || return 1
  # No resize here: the window takes its size from the attached client, or from
  # default-size when none is attached.
  # pipe-pane must name the phase window, not the session.
  [ -z "$logfile" ] || "$TMUX_BIN" pipe-pane -t "$wid" -o "cat >> '$logfile'" 2>/dev/null || true
  return 0
}

# Drop a dead-but-retained phase window so the next launch can proceed. Only ever
# touches the phase window; the session and the idle window are left alone.
tmux_phase_window_close() {
  local wid
  wid="$(tmux_window_id)" || return 0
  "$TMUX_BIN" kill-window -t "$wid" 2>/dev/null || return 0
  log "reclaimed dead phase window in '$SESSION'"
  return 0
}

# Set the terminal title. A literal string, not a format: set-titles-string is a
# session option, and a format like #{window_name} would follow whichever window
# the attached client happens to be viewing. Verified that a literal reaches an
# attached client on its own, with no refresh-client, in well under a second.
tmux_set_title() {
  "$TMUX_BIN" set-option -t "$SESSION" set-titles-string "$1" 2>/dev/null || true
}

# Rename the phase window for display. The name is a label, NOT an identity:
# every lookup here goes through tmux_window_id, so a renamed window is still
# found. Renaming is safe for that reason; renaming and then looking the window
# up by name would not be.
tmux_phase_window_rename() {
  local wid
  wid="$(tmux_window_id)" || return 0
  "$TMUX_BIN" rename-window -t "$wid" "$1" 2>/dev/null || true
}

# Interrupt the phase, not the idle window. Targets the window ID so this is
# correct no matter what the window has been renamed to, and so it can never
# land on the idle display.
tmux_phase_window_interrupt() {
  local wid
  wid="$(tmux_window_id)" || return 1
  "$TMUX_BIN" send-keys -t "$wid" C-c 2>/dev/null || return 1
  return 0
}

# Select the phase window so an attached client shows the work, not the tail.
tmux_phase_window_select() {
  local wid
  wid="$(tmux_window_id)" || return 0
  "$TMUX_BIN" select-window -t "$wid" 2>/dev/null || true
}

# Size of the phase window as WxH, for --check and the self-test.
tmux_phase_window_geometry() {
  local wid
  wid="$(tmux_window_id)" || return 0
  "$TMUX_BIN" display-message -p -t "$wid" \
    '#{window_width}x#{window_height}' 2>/dev/null || true
}
