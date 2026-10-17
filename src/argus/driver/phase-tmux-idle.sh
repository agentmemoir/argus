#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Idle window content for the persistent tmux session.
#
# Run as the idle window's command by argus/driver/phase-tmux.sh. It is the only
# thing standing between a quiet line and a dead session: a window whose command
# exits closes, and if it is the last window the session and possibly the whole
# tmux server go with it. So this script keeps looping, even when every file it
# reads is missing, and ends only when the process that started it is gone. That
# is the window's shell or the tmux server; with it gone there is no window left
# to hold open, and an unattended loop would be an orphan with no one to stop it.
#
# A session the self-test made is named after the run that owns it, so the last
# field of its name is that run's pid and this script ends the session when that
# run is gone. See argus/driver/lib-selftest-recover.sh. That is the one thing a
# killed run cannot do for itself: SIGKILL runs no trap, so without this the
# monitor redraws for as long as the session lives, and the session lives
# forever. A production session is not named that way, so it has no anchor and
# behaves exactly as it always did.
#
# Shows, in order:
#   - a HALTED banner with the reason when the line is halted (Q2: the title
#     also carries it, because a TV may not display a titlebar at all)
#   - a status block: current phase, last result, time since last activity
#   - the tail of the driver log
#
# Reads only. It writes nothing and starts nothing, so it cannot perturb a run.

set -uo pipefail

# The starter, read before anything else. Its pid stays here after the script is
# adopted by init, which is how the loop below tells that the starter has gone.
STARTER="$PPID"

# The idle window loads the same config file as the driver. Its CLI flags are
# passed when the window is created, so it sees the same one-run overrides.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The idle script sits beside the driver, in `driver/`; the Python tools it
# runs sit one level up, in the role packages of the package root.
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$(command -v python3 2>/dev/null || true)"
if [ -z "$PYTHON" ]; then
  printf 'argus idle: python3 is needed to read the config file\n' >&2
  exit 2
fi
CONFIG_VARS=(
  REPO PIPELINE PHASES_REPO PHASES_DIRECTORY PHASES_BRANCH PHASES_REMOTE PHASE_NUMBER_INPUT PHASE_FILE_INPUT DRIVER
  PHASE_INCLUDE PHASE_EXCLUDE WF DRIVER_STATE STALE_AFTER_SEC OUTAGE_STALE_SEC
  QUIET_MINUTES LOG_KEEP_DAYS MERGE_CHECK_MAX RECOVER_OWN_CLAIM PHASE_MACHINE_ID
  CMD_GLIDE CMD_GIT CMD_TMUX CMD_PYTHON CMD_HERMES CMD_GH SESSION_NAME
  TMUX_WIDTH TMUX_HEIGHT TMUX_IDLE_WINDOW TMUX_PHASE_WINDOW TMUX_IDLE_SCRIPT
  TMUX_REFRESH BOARD_REPO BOARD_FILE BOARD_BRANCH BOARD_BACKUP_BRANCH BOARD_REMOTE
  HERMES_PROFILE_NAME DISCORD_CHANNEL NOTIFY_DISABLED DISCORD_FALLBACK_CHANNEL
  HEARTBEAT_URL
)
for name in "${CONFIG_VARS[@]}"; do
  unset "$name" "ARGUS_SOURCE_$name"
done
CONFIG_OUT="$("$PYTHON" "$PKG_DIR/config/cli.py" "$@" --format shell)" || exit 2
eval "$CONFIG_OUT"
PYTHON="$CMD_PYTHON"
REPO="$(cd "$REPO" && pwd)"

# The run this session belongs to, when the session is one the self-test made.
# The name is read from tmux rather than from the configuration, because the case
# that created the session may have named it something the configuration file
# does not say.
SESSION_NOW=""
ANCHOR=""
if [ -n "${TMUX_PANE:-}" ]; then
  SESSION_NOW="$("$CMD_TMUX" display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null || true)"
  case "$SESSION_NOW" in
    ar-driver-*-[0-9]*) ANCHOR="${SESSION_NOW##*-}" ;;
  esac
fi

# Portable mtime helper, shared with the driver so the fix lives in one place.
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib-stat.sh"
RUN_DIR="$WF/$DRIVER_STATE"
LOG="$RUN_DIR/driver.log"
HALT="$RUN_DIR/halted"
CURRENT="$RUN_DIR/current"
STALL="$RUN_DIR/stalled"
SESSION="$SESSION_NAME"
REFRESH="$TMUX_REFRESH"

# Longest run we will show. Keeps a long log from scrolling the banner away.
TAIL_LINES=15

human_age() {
  local secs="$1"
  [ "$secs" -ge 0 ] 2>/dev/null || secs=0
  if [ "$secs" -lt 60 ]; then printf '%ds ago' "$secs"
  elif [ "$secs" -lt 3600 ]; then printf '%dm ago' "$((secs / 60))"
  elif [ "$secs" -lt 86400 ]; then printf '%dh ago' "$((secs / 3600))"
  else printf '%dd ago' "$((secs / 86400))"
  fi
}

file_age() {
  local f="$1" now mtime
  [ -f "$f" ] || { printf 'never'; return 0; }
  # stat_epoch picks the right `stat` form for this platform and always
  # returns a bare integer. See argus/driver/lib-stat.sh for why the obvious
  # `stat -f %m || stat -c %Y` chain is wrong on Linux.
  mtime="$(stat_epoch "$f")"
  now="$(date +%s)"
  human_age "$((now - mtime))"
}

last_result() {
  local line
  line="$(grep -E 'completed|HALTED|blocked|rejected' "$LOG" 2>/dev/null \
    | grep -v 'notify' | tail -1)"
  [ -n "$line" ] && printf '%s' "${line#* }" || printf 'no run recorded yet'
}

# The whole body is guarded: a missing file, an unreadable one, or a grep that
# matches nothing must all still produce a screen and loop again.
draw() {
  local cur banner
  clear 2>/dev/null || printf '\033[H\033[2J'
  printf '\033[1mclio unattended line\033[0m   session: %s\n\n' "$SESSION"

  if [ -f "$HALT" ]; then
    # Big banner first. This is the case a TV must never miss, and the title
    # alone is not enough because the titlebar may not be visible.
    printf '\033[1;41;97m  HALTED  \033[0m  %s\n\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf '  reason: '
    tr '\n' ' ' <"$HALT" 2>/dev/null | cut -c1-100
    printf '\n\n'
  else
    printf '\033[1;32m  RUNNING\033[0m  (no halt marker)\n\n'
  fi

  cur="$(cat "$CURRENT" 2>/dev/null || true)"
  printf '  %-14s %s\n' 'phase now:' "${cur:-none}"
  printf '  %-14s %s\n' 'last result:' "$(last_result)"
  printf '  %-14s %s\n' 'last log:' "$(file_age "$LOG")"
  if [ -f "$STALL" ]; then
    printf '  %-14s %s\n' 'stalled:' "$(tr '\n' ' ' <"$STALL" 2>/dev/null | cut -c1-60)"
  fi

  printf '\n  ---- driver log (last %s lines) ----\n' "$TAIL_LINES"
  tail -n "$TAIL_LINES" "$LOG" 2>/dev/null || printf '  (no log yet at %s)\n' "$LOG"
  printf '\n'
}

# Loop while the starter lives. `draw` is allowed to fail; the sleep keeps the
# window alive either way, which is the property that protects the session.
while kill -0 "$STARTER" 2>/dev/null; do
  # A run that owns this session and is gone leaves nothing to show, so the
  # session goes with it. Killing the session also closes the phase window, which
  # is what the run's own teardown would have done; letting this window merely
  # exit would leave the session standing while any other window lives.
  if [ -n "$ANCHOR" ] && ! kill -0 "$ANCHOR" 2>/dev/null; then
    "$CMD_TMUX" kill-session -t "$SESSION_NOW" 2>/dev/null || true
    exit 0
  fi
  draw || true
  sleep "$REFRESH" || sleep 5
done
