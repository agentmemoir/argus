#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Recovery of a self-test run that was killed too hard for its teardown to run.
#
# SIGKILL runs no trap, so the teardown in `lib-selftest.sh` cannot be the only
# cleanup. A killed run leaves its fixture directory behind, with the tmux server
# it started and the idle monitor inside that server's session.
#
# Two mechanisms cover that, and they are deliberately independent:
#
#   - `phase-tmux-idle.sh` ends the session itself when the run that owns it is
#     gone, which is the fast path: the session, its windows and its monitor go
#     within one refresh interval of the kill.
#   - `selftest_recover_leftovers`, below, is the backstop, and it owns what no
#     session can clean up by itself: the fixture directory on disk.
#
# The run records its own pid and the socket directory it started the server on
# in `<fixture>/owner`. The recorded pid is the run's own, so the liveness test
# cannot go wrong in the dangerous direction: a live run always reads as live and
# is skipped, and a wrong answer the other way only leaves the leak where it was.
# `kill -0` is the portable liveness test here; pid reuse is real, and the
# kernel's answer to it is a pidfd, which a shell script does not have. Reuse can
# only read a dead run as alive, which delays recovery rather than touching a
# live run.
#
# A fixture with no record at all was made before this record existed, and there
# age is the only signal there is. That is why the age rule stays: two self-tests
# on one machine must not delete each other's fixture, and without something else
# to go on a fresh directory is indistinguishable from an abandoned one.
#
# Nothing here may decide whether a run happens. Every failure is reported and
# then swallowed, because a leftover is not a reason to skip every check.

# The directories a fixture can be in, asked of the helper that chooses one
# rather than copied here, so the two cannot drift apart.
#
# The helper's first candidate is the default temporary directory, which is what
# `TMPDIR` names when it is set and where the fixture really goes on macOS. A
# sweep that named `/tmp` alone never looked there, so a killed run's fixture and
# its sessions stayed until someone removed them by hand.
#
# A caller where the helper cannot be asked falls back to the list it last had,
# because a sweep with no bases is a sweep that recovers nothing.
#
# `ARGUS_SELFTEST_BASES` names one base instead of every base on the machine,
# which is how a suite keeps the sweep off every other run's directories.
selftest_fixture_bases() {
  # The override is read before anything else touches `$ARGUS`, so a caller that
  # named a base needs nothing of the driver's environment.
  if [ -n "${ARGUS_SELFTEST_BASES:-}" ]; then
    printf '%s\n' "$ARGUS_SELFTEST_BASES"
    return 0
  fi
  local helper="$ARGUS/exec_tempdir.py" bases=""
  if [ -f "$helper" ]; then
    bases="$("$PYTHON" - "$helper" <<'PY' 2>/dev/null
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
try:
    from exec_tempdir import candidate_bases
except ImportError:
    raise SystemExit(1)
for base in candidate_bases():
    print(base)
PY
)"
  fi
  if [ -n "$bases" ]; then
    printf '%s\n' "$bases"
    return 0
  fi
  printf '%s\n' "${TMPDIR:-/tmp}" /tmp /dev/shm "$(dirname "$ARGUS")/target"
}

# Stop the sessions that belong to one run, and answer whether they are gone.
#
# The run's own pid is the last field of every session name it makes, so this
# reaches that run's sessions and no others: a caller may point two runs at one
# socket directory, and the other run's sessions do not end in this pid.
#
# `0` means the sessions are gone, or there was nothing left to stop, so the
# fixture may be removed. `1` means they are still there, or tmux could not be
# asked at all, and the caller must leave the fixture alone: removing it unlinks
# the socket of a server that is still running, and that is the one way to make a
# leaked monitor impossible to find.
selftest_stop_run_sessions() {
  local tmux_bin="$1" socket="$2" pid="$3" listed="" session
  command -v "$tmux_bin" >/dev/null 2>&1 || return 1
  listed="$(env TMUX_TMPDIR="$socket" "$tmux_bin" list-sessions -F '#{session_name}' 2>/dev/null)" || return 0
  for session in $listed; do
    case "$session" in
      *-"$pid")
        env TMUX_TMPDIR="$socket" "$tmux_bin" kill-session -t "$session" 2>/dev/null || true
        ;;
    esac
  done
  listed="$(env TMUX_TMPDIR="$socket" "$tmux_bin" list-sessions -F '#{session_name}' 2>/dev/null)" || return 0
  for session in $listed; do
    case "$session" in
      *-"$pid") return 1 ;;
    esac
  done
  return 0
}

# Recover what an earlier run left behind. Reads the fixture bases from
# `selftest_fixture_bases`, so a caller can point the sweep at one base of its own
# instead of every base on the machine.
selftest_recover_leftovers() {
  local tmux_bin="${CMD_TMUX:-tmux}" base leftover owner key value pid socket
  while IFS= read -r base; do
    for leftover in "$base"/argus-selftest-*; do
      [ -d "$leftover" ] || continue
      pid="" socket=""
      owner="$leftover/owner"
      # `-r` and not `-f`: a record that cannot be read is treated as no record
      # and swept on age, because a failed read here would return non-zero to the
      # caller and stop the whole run before a single check.
      if [ -r "$owner" ]; then
        while IFS='=' read -r key value; do
          case "$key" in
            pid) pid="$value" ;;
            socket) socket="$value" ;;
          esac
        done <"$owner"
      fi
      if [ -n "$pid" ]; then
        if kill -0 "$pid" 2>/dev/null; then
          continue
        fi
      else
        # `find` prints the directory only when it is older than the age asked
        # for, so an empty answer means too young to be abandoned.
        [ -n "$(find "$leftover" -maxdepth 0 -mmin +60 2>/dev/null)" ] || continue
      fi
      [ -n "$socket" ] || socket="$leftover/tmux"
      if [ -n "$pid" ] && ! selftest_stop_run_sessions "$tmux_bin" "$socket" "$pid"; then
        printf 'self-test: run %s left %s behind and its sessions are still running; leaving it in place\n' \
          "$pid" "$leftover" >&2
        continue
      fi
      # A socket inside the fixture can only belong to the fixture, so its server
      # goes with it. This is the path a recordless fixture takes, and the one a
      # recorded fixture takes once its sessions are confirmed gone.
      case "$socket" in
        "$leftover"/*)
          env TMUX_TMPDIR="$socket" "$tmux_bin" kill-server >/dev/null 2>&1 || true
          ;;
      esac
      rm -rf "$leftover" 2>/dev/null || true
    done
  done < <(selftest_fixture_bases)
}
