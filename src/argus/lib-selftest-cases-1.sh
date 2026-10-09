#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: launch, lock capture, teardown, tripwire, and stall.
#
selftest_case_launch() {

  # No coordination stub here, and that is the point of the throwaway checkout
  # above. These cases used to run on `DRIVER_STUB_COORDINATION=fence` for a good
  # reason: a tick with no stub asks the real selector which phase is next and
  # then asks the sync helper to put the public checkout on that phase's branch,
  # and doing that to a developer's real checkout moved it, twice, before anyone
  # recorded that it could. The stub was the only thing standing between this
  # suite and someone's working tree, and it had a second cost nobody named: the
  # stub skips the very steps that move a checkout, so the steps a test most needs
  # to cover were the ones no test covered.
  #
  # With a fixture there is nothing left to protect, so the stub goes and the
  # cases run the production path for real: the real selector, the real
  # reservation, the real sync, the real branch. What they assert is unchanged.
  #
  # The cases further down still stub coordination, and legitimately: they inject
  # answers the selector cannot produce, such as a board that is unreachable or a
  # state name this driver has no case for. A stub for a shape no real run can
  # produce is a different thing from a stub standing in for the real thing.
  RECOVER_OWN_CLAIM=1; export RECOVER_OWN_CLAIM

  drive --self-test || true
  tmux_session_exists \
    && echo "ok: launched a session" || { echo "FAIL: session not created"; fail=1; }
  tmux_window_exists \
    && echo "ok: phase window opened" || { echo "FAIL: phase window not opened"; fail=1; }
  tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx -- idle \
    && echo "ok: idle window present" || { echo "FAIL: idle window missing"; fail=1; }
  geom="$(tmux_phase_window_geometry)"
  [ "$geom" = "${TMUX_WIDTH}x${TMUX_HEIGHT}" ] \
    && echo "ok: phase window geometry $geom" \
    || { echo "FAIL: geometry ${geom:-gone}, want ${TMUX_WIDTH}x${TMUX_HEIGHT}"; fail=1; }
  case "$(title_of)" in
    *"starting"*|*"running"*) echo "ok: running title set ($(title_of))" ;;
    *) echo "FAIL: running title not set, got '$(title_of)'"; fail=1 ;;
  esac
  drive --self-test || true
  grep -q "running; skip" "$LOG" \
    && echo "ok: busy run skipped" || { echo "FAIL: busy run not skipped"; fail=1; }
  sleep 1
  start_count="$(grep -c 'Phase .* started\.' "$LOG" || true)"
  [ "$start_count" = 1 ] \
    && echo "ok: one start notification logged" || { echo "FAIL: expected one start notification, got $start_count"; fail=1; }
  grep -q '\[clio\]' "$LOG" \
    && { echo "FAIL: old notification prefix remains"; fail=1; } \
    || echo "ok: notification prefix removed"

  wait_phase_window_gone
  grep -q "Phase .* has completed\." "$LOG" \
    && echo "ok: completion notification logged" || { echo "FAIL: completion notification missing"; fail=1; }

  # The whole point of the change: the session outlives the phase.
  tmux_session_exists \
    && echo "ok: session survives phase completion" \
    || { echo "FAIL: session died with the phase"; fail=1; }
  tmux_window_exists \
    && { echo "FAIL: phase window still present after completion"; fail=1; } \
    || echo "ok: phase window closed on completion"
  tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx -- idle \
    && echo "ok: idle window survives phase" || { echo "FAIL: idle window gone after phase"; fail=1; }
  case "$(title_of)" in
    *"done"*) echo "ok: completion title set ($(title_of))" ;;
    *) echo "FAIL: completion title not set, got '$(title_of)'"; fail=1 ;;
  esac
  # An idle session must not block the next tick. Count skips before and after
  # rather than grepping the whole log: an earlier legitimate skip (while the
  # first phase ran) is still in there and would mask a real regression.
  skips_before="$(grep -c 'phase window is running; skip' "$LOG" || true)"
  drive --self-test || true
  skips_after="$(grep -c 'phase window is running; skip' "$LOG" || true)"
  if [ "$skips_after" -gt "$skips_before" ]; then
    echo "FAIL: idle session blocked the line"; fail=1
  else
    echo "ok: idle session does not block the line"
  fi
  wait_phase_window_gone

  wait_phase_window_gone

}

selftest_case_locks_stall() {
  # A tick whose output is captured in a command substitution.
  #
  # The lock a tick takes is created inside the function that takes it, and a
  # shell drops a function's local variables as soon as the function returns. An
  # EXIT trap that removes the lock therefore cannot see it by the time the trap
  # runs in some shells, and the lock is left behind; every later tick then finds a
  # lock it did not take and skips, so the driver stops ticking while still
  # reporting success. That is the worst shape a scheduler failure can take, and
  # it is invisible from the log, which says only that a tick ran.
  #
  # A command substitution is the trigger, not decoration: it runs the body in a
  # subshell, and a subshell's trap runs where the enclosing function's locals are
  # already gone. Two ticks in one process is the observation that matters, so
  # this counts skips across the capture the same way the idle case above does,
  # and it separately confirms the captured tick really ran, because a tick that
  # did nothing would pass the skip check for the wrong reason.
  # The lock is asserted by the path that this host actually takes. On a host
  # with flock(1) the lock is an advisory lock on an open file descriptor, and
  # the file itself stays on disk after the tick releases it: a persisted
  # `driver.lock` is normal and not a leak. The portable path is what leaked —
  # it made a directory and removed it on exit — so its absence is asserted, and
  # the skip count below proves release on both paths. Growth of the log is what
  # proves the captured tick ran at all: a tick that decided there was nothing to
  # do writes a line and leaves, so the log grows either way, whereas a tick that
  # never started would leave it untouched and pass the lock check for the wrong
  # reason.
  sub_lines_before="$(wc -l <"$LOG" | tr -d '[:space:]')"
  _captured="$(drive --self-test 2>&1)" || true
  sub_lines_after="$(wc -l <"$LOG" | tr -d '[:space:]')"
  # Count the two lock messages and not the phase-window one. A tick that finds
  # an open phase window skips too, and that is correct behaviour, so counting it
  # here would fail the case for a reason that has nothing to do with the lock.
  sub_skips_before="$(grep -c 'holds the portable lock; skip\|holds the lock; skip' "$LOG" || true)"
  if [ "$sub_lines_after" -le "$sub_lines_before" ]; then
    echo "FAIL: the tick captured in a substitution did not run, so the lock check proves nothing"
    fail=1
  elif [ -e "$LOCK.d" ]; then
    echo "FAIL: a tick captured in a substitution left its portable lock behind ($LOCK.d)"
    fail=1
  else
    drive --self-test || true
    sub_skips_after="$(grep -c 'holds the portable lock; skip\|holds the lock; skip' "$LOG" || true)"
    if [ "$sub_skips_after" -gt "$sub_skips_before" ]; then
      echo "FAIL: a tick captured in a substitution left a lock that stopped the next tick"
      fail=1
    else
      echo "ok: a tick captured in a substitution leaves no lock behind"
    fi
  fi
  unset _captured
  wait_phase_window_gone

  # The teardown trap, on the path it exists for.
  #
  # The leak it closes was one orphaned tmux session per run, and that session's
  # idle monitor goes on forking for minutes after the run prints its last line,
  # which is how twenty runs of this suite made a machine unresponsive. A trap
  # that has only ever been reached on the successful path has not been tested at
  # all, and the successful path is the one that needed it least: the leak
  # happened when a run stopped half way and never reached the teardown at the end
  # of the body.
  #
  # So this runs the teardown the way a dying run runs it. A child creates a real
  # session and a real fixture folder, installs the same trap, and then stops
  # early the way this suite always has, on an unbound variable under `set -u`.
  # From here the question is whether either survived, which is the only thing
  # the trap promises.
  abort_name="ar-driver-selftest-abort-$$"
  abort_tmp="$tmp/abort-fixture"
  mkdir -p "$abort_tmp"
  (
    export SELFTEST_TMP="$abort_tmp"
    export SELFTEST_SESSION="$abort_name"
    # This child is standing in for a run that stops half way, and the whole
    # point is that the trap removes its fixture. A keep flag on the parent
    # would exempt it, and the case would pass without testing the trap.
    SELFTEST_KEEP_TMP=0
    export SELFTEST_KEEP_TMP
    SELFTEST_REACHED_END=0
    export SELFTEST_REACHED_END
    tmux new-session -d -s "$abort_name" 'sleep 30' 2>/dev/null || true
    trap selftest_teardown EXIT HUP INT TERM
    printf 'unreached %s\n' "${NO_SUCH_VARIABLE}"
  ) >/dev/null 2>&1 || true
  # The child has to be given the moment it needs to create the session and then
  # abandon it, or the question answered is "has one appeared yet" rather than
  # "does one survive".
  sleep 1
  if tmux has-session -t "$abort_name" 2>/dev/null; then
    echo "FAIL: the teardown left the session behind when a run stopped half way"
    tmux kill-session -t "$abort_name" 2>/dev/null || true
    fail=1
  elif [ -d "$abort_tmp" ]; then
    echo "FAIL: the teardown left the fixture folder behind when a run stopped half way"
    fail=1
  else
    echo "ok: a run that stops half way still has its session and fixture removed"
  fi
  unset abort_name abort_tmp

  # The tripwire's own failure path, which has never once been observed to fire.
  #
  # A check that has only ever printed its success line has not been tested, and
  # this repository has already shipped a check that quietly declined to run; the
  # habit of trusting one because it looks like it works is what that costs. So
  # the tripwire is made to see a change here and has to report it.
  #
  # The snapshot is falsified rather than a real checkout being moved. The real
  # git reads still run, so the comparison, the reporting and the exit status are
  # all exercised; what is skipped is mutating a working tree mid-suite, which
  # would put every later case at the mercy of this one. Two of the three signals
  # are checked this way, because they are three separate branches in the function
  # and one of them being unreachable would be invisible otherwise.
  tripwire_saved_public="$SELFTEST_REAL_PUBLIC_STATE"
  tripwire_saved_board="$SELFTEST_REAL_BOARD_STATE"
  tripwire_saved_runs="$SELFTEST_REAL_RUNS"
  tripwire_saved_marker="$SELFTEST_REAL_RUNS_MARKER"
  tripwire_probe_dir="$tmp/tripwire-probe-runs"
  mkdir -p "$tripwire_probe_dir"
  tripwire_missed=""
  for tripwire_signal in checkout board runstate; do
    case "$tripwire_signal" in
      checkout) SELFTEST_REAL_PUBLIC_STATE="master 0000000000000000000000000000000000000000 changed=[]" ;;
      board) SELFTEST_REAL_BOARD_STATE="0000000000000000000000000000000000000000" ;;
      runstate)
        SELFTEST_REAL_RUNS="$tripwire_probe_dir"
        SELFTEST_REAL_RUNS_MARKER="$tripwire_probe_dir/marker"
        : >"$SELFTEST_REAL_RUNS_MARKER"
        printf 'written after the marker\n' >"$tripwire_probe_dir/run.json"
        ;;
    esac
    if selftest_real_state_violations >"$tmp/tripwire-$tripwire_signal.out" 2>&1; then
      tripwire_missed="$tripwire_missed $tripwire_signal"
    fi
    rm -f "$tripwire_probe_dir/run.json"
  done
  SELFTEST_REAL_PUBLIC_STATE="$tripwire_saved_public"
  SELFTEST_REAL_BOARD_STATE="$tripwire_saved_board"
  SELFTEST_REAL_RUNS="$tripwire_saved_runs"
  SELFTEST_REAL_RUNS_MARKER="$tripwire_saved_marker"
  if [ -n "$tripwire_missed" ]; then
    echo "FAIL: the checkout tripwire did not report a change in:$tripwire_missed"
    for tripwire_signal in $tripwire_missed; do
      sed 's/^/      /' "$tmp/tripwire-$tripwire_signal.out" 2>/dev/null | head -4
    done
    fail=1
  else
    echo "ok: the checkout tripwire reports a moved checkout, a moved board and a written run state"
  fi
  unset tripwire_saved_public tripwire_saved_board tripwire_saved_runs \
        tripwire_saved_marker tripwire_probe_dir tripwire_missed tripwire_signal

  # Exercise the stall path without waiting for the real threshold. Use this
  # shell as the live process so the test does not race the short stub run.
  old_stale_after_sec="$STALE_AFTER_SEC"
  stall_test_phase=100000
  printf '%s\n' "$stall_test_phase" >"$CURRENT"
  printf '%s\n' "$$" >"$PIDFILE"
  # The directory the check measures is built here, and only the file inside it
  # carries the age. `check_stall` reads the newest mtime under
  # `$WF/phase-<number>`; a directory it never created measures nothing, the
  # check returns before it can notify, and the test reports a missing
  # notification for a host that is working exactly as designed.
  stall_dir="$WF/phase-$(printf '%06d' "$stall_test_phase")"
  mkdir -p "$stall_dir"
  : >"$stall_dir/activity"
  : >"$RUN_DIR/session-$stall_test_phase.log"
  STALE_AFTER_SEC=0
  # Age is measured in whole seconds and the comparison is strictly greater, so
  # the file has to be at least one second old before the check runs. Sleeping
  # before the check rather than after the write is what makes that true; writing
  # last would leave a measured age of zero and the check would correctly decline
  # to fire.
  sleep 1
  check_stall
  stall_count="$(grep -c 'Phase .* looks stalled\.' "$LOG" || true)"
  [ "$stall_count" = 1 ] && [ -f "$STALL" ] \
    && echo "ok: one stall notification logged" || { echo "FAIL: expected one stall notification, got $stall_count"; fail=1; }
  check_stall
  stall_count_after="$(grep -c 'Phase .* looks stalled\.' "$LOG" || true)"
  [ "$stall_count_after" = "$stall_count" ] \
    && echo "ok: stall notification is not repeated" || { echo "FAIL: stall notification repeated"; fail=1; }
  rm -rf "$stall_dir"
  rm -f "$STALL" "$CURRENT" "$PIDFILE"
  STALE_AFTER_SEC="$old_stale_after_sec"

  DRIVER_STUB_RC=1; export DRIVER_STUB_RC
  rm -f "$HALT"
  drive --self-test || true
  wait_phase_window_gone
  [ -f "$HALT" ] \
    && echo "ok: halt marker written" || { echo "FAIL: halt marker missing"; fail=1; }
  grep -q "Phase .* stopped\." "$LOG" \
    && echo "ok: halt notification logged" || { echo "FAIL: halt notification missing"; fail=1; }
  case "$(title_of)" in
    *HALTED*) echo "ok: halt title set ($(title_of))" ;;
    *) echo "FAIL: halt title not set, got '$(title_of)'"; fail=1 ;;
  esac

  drive --self-test || true
  grep -q "refusing to advance" "$LOG" \
    && echo "ok: refuses to advance while halted" || { echo "FAIL: advanced while halted"; fail=1; }
  tmux_window_exists \
    && { echo "FAIL: opened a phase window while halted"; fail=1; } \
    || echo "ok: no phase window opened while halted"

}
