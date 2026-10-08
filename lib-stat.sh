#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Portable file-modification-time helper.
#
# Sourced by argus/phase-driver.sh and argus/phase-tmux-idle.sh. Not
# executable on its own. Kept in one place because this exact bug has now been
# written twice, in two files, and both copies shipped.
#
# WHY THIS EXISTS
# The obvious one-liner is wrong on Linux:
#
#   stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0
#
# `stat -f` is the BSD/macOS form. On GNU coreutils AND on uutils coreutils
# (what the Ubuntu server runs) `-f` means --file-system, so `%m` is read as a
# device name and the command prints a filesystem block table to stdout. The
# nasty part is the exit status: GNU exits 1, but uutils exits 0. So on the
# server the `||` chain stops at the *first* branch, `|| echo 0` never runs, and
# the caller receives a multi-line block table where it expected a number.
# Downstream `[ "$m" -gt "$n" ]` then fails on every comparison.
#
# The fix is to probe for the form this system supports and then use only that
# one, so a wrong form can never leak output into the caller's variable.

# Echo the file's modification time as epoch seconds, or 0 if it cannot be read.
# Always echoes a bare integer, so callers may do arithmetic on it unguarded.
stat_epoch() {
  local f="$1" v=''
  [ -f "$f" ] || { printf '0'; return 0; }
  # GNU and uutils both accept -c %Y; BSD/macOS accepts -f %m.
  if stat -c %Y "$f" >/dev/null 2>&1; then
    v="$(stat -c %Y "$f" 2>/dev/null)"
  else
    v="$(stat -f %m "$f" 2>/dev/null)"
  fi
  case "$v" in
    ''|*[!0-9]*) v=0 ;;
  esac
  printf '%s' "$v"
}

# Echo the newest modification time across the given files, or 0 if none exist.
stat_newest_epoch() {
  local newest=0 f m
  for f in "$@"; do
    [ -f "$f" ] || continue
    m="$(stat_epoch "$f")"
    [ "$m" -gt "$newest" ] && newest="$m"
  done
  printf '%s' "$newest"
}
