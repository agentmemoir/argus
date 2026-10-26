#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# One checkout, one driver. Two driver configs that name the same repository root
# must not change its working tree at the same time (issue 199).
#
# The lock is an exclusive flock on argus-driver-checkout.lock in the repository's
# git common directory, so every worktree of one repository shares it. The phase
# process takes it when it starts, on descriptor 8, and holds it until it exits.
# The kernel releases a flock when its last descriptor closes, so a crash cannot
# leave a stale lock behind. The file's text names the holding config's basename
# for the refusal line; the lock itself, not the text, decides.
#
# The tick does not keep the lock. It probes it in a subshell and lets go at once,
# so a tick that refuses holds nothing. The phase takes the lock inside its own
# process: the tmux server forks each phase window, so a descriptor the tick
# opened would never reach the phase.
#
# Descriptor 8 is new. Descriptor 9 is the driver lock, and descriptor 3 is cron's
# flock. A tmux server that inherits an open lock descriptor keeps the lock for as
# long as the server lives (#224, #234). The tick's own tmux calls never run while
# this descriptor is open, and a phase only uses tmux after it has taken the lock.
#
# The test uses Python's fcntl, not flock(1), because macOS has no flock command.

# The lock file in the checkout's git common directory. Fails when REPO is not a
# git checkout, so there is no shared checkout to guard.
driver_checkout_lock() {
  local common
  common="$(${CMD_GIT:-git} -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  [ -n "$common" ] || return 1
  printf '%s/argus-driver-checkout.lock\n' "$common"
}

# Try the exclusive lock on descriptor 8. Returns 0 when it is won. The caller
# must hold descriptor 8 open, or the lock is released when this returns.
driver_checkout_flock() {
  "$PYTHON" -c 'import fcntl; fcntl.flock(8, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null
}

# Returns 0 when another driver holds the checkout lock. The probe takes the lock
# inside a subshell and drops it on exit. A checkout with no lock file path is not
# held.
driver_checkout_held() {
  local lock
  lock="$(driver_checkout_lock)" || return 1
  if ( exec 8<>"$lock" && driver_checkout_flock ); then
    return 1
  fi
  return 0
}

# The one refusal line. It names the holding config by basename only, so no host
# path reaches a log, a notice, or a check report.
driver_checkout_refusal() {
  local lock holder=""
  lock="$(driver_checkout_lock)" && holder="$(head -n 1 "$lock" 2>/dev/null | tr -cd 'A-Za-z0-9._-')"
  printf 'argus: checkout is in use by driver config %s; give each driver its own checkout\n' \
    "${holder:-another config}"
}

# Phase process: take the lock for the whole run. Returns 1 when another driver
# holds it, and the caller stops before any work. Writes this config's basename
# into the lock file so a refused driver can name it.
driver_checkout_take() {
  local lock
  lock="$(driver_checkout_lock)" || return 0
  exec 8<>"$lock" || return 1
  driver_checkout_flock || return 1
  printf '%s\n' "$(basename "${ARGUS_CONFIG_FILE:-config}")" >"$lock"
}

# Readiness report (`--check`): print the refusal line when another driver holds
# the checkout. Reports only, and never changes the check's exit status.
driver_checkout_report() {
  if driver_checkout_held; then
    driver_checkout_refusal
  else
    echo "checkout lock: free"
  fi
}
