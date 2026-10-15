#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: the abandon close-out.
#
# The residue is what the selector reads to skip and report a parked phase, and
# it is the replacement for the hand tick that used to close a blocked phase out.

selftest_case_abandon() {
  local phase=100010 rel run_dir old_fence lock_out lock_rc
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  old_fence="${DRIVER_STUB_BOARD_FENCE:-}"
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  wait_phase_window_gone
  rm -f "$HALT" "$HALT_RECORD" "$CLAIM" "$run_dir/abandoned.json"

  # The shared lock: while another holder has it, abandon refuses rather than
  # racing a resume. This shell takes the lock, then abandon runs in a subshell
  # that reopens the lock file and finds a second holder where flock exists. On a
  # host with no flock there is no lock to test, so the case is skipped.
  if command -v flock >/dev/null 2>&1; then
    driver_lock_take || true
    lock_out="$( ( abandon_cmd "$phase" ) 2>&1 )" && lock_rc=0 || lock_rc=$?
    if [ "${lock_rc:-0}" -ne 0 ] \
        && printf '%s' "$lock_out" | grep -q 'holds the lock'; then
      echo "ok: abandon refuses while another driver holds the lock"
    else
      echo "FAIL: abandon ran while another driver held the lock"; fail=1
    fi
    exec 9>&- || true
  else
    echo "skip: abandon lock case (this host has no flock)"
  fi

  # A phase with no run directory is nothing to abandon. Parking it would hide
  # work the queue never did and would never retry. A subshell, because
  # `abandon_cmd` exits on refusal and that must not end the suite. The suite's
  # inherited lock is released first, as the resume cases do: `abandon_cmd` takes
  # it again, and an fd inherited from this shell would count as a second holder.
  exec 9>&- || true
  ( abandon_cmd 100499 ) >/dev/null 2>&1 || true
  if [ ! -f "$(phase_run_dir 100499 "")/abandoned.json" ]; then
    echo "ok: abandon refuses a phase with no run directory"
  else
    echo "FAIL: abandon wrote a residue for a phase that never ran"; fail=1
  fi

  # The residue is what the selector reads to skip and report the phase, and the
  # record is what a resume would read: a parked phase must not resume, so the
  # record goes and the run directory stays.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  exec 9>&- || true
  ( abandon_cmd "$phase" --reason 'the work is no longer wanted' ) >/dev/null 2>&1 || true
  if [ -f "$run_dir/abandoned.json" ] && [ ! -f "$HALT_RECORD" ] \
      && [ ! -f "$CLAIM" ] && [ -d "$run_dir" ]; then
    echo "ok: abandon writes the residue, releases the claim, and keeps the run directory"
  else
    echo "FAIL: abandon did not write the residue or clear the record"; fail=1
  fi
  if grep -q "abandoned phase $phase" "$LOG"; then
    echo "ok: abandon says so in the driver log"
  else
    echo "FAIL: abandon wrote no driver log line"; fail=1
  fi

  # A live recorded pid with no phase window is an orphaned run. Parking the phase
  # would delete the evidence a person needs, so abandon refuses and writes no
  # residue. The marker and the pid file survive.
  rm -f "$run_dir/abandoned.json"
  printf 'halted for the orphan case\n' >"$HALT"
  printf '%s\n' "$$" >"$PIDFILE"
  exec 9>&- || true
  ( abandon_cmd "$phase" ) >/dev/null 2>&1 || true
  if [ ! -f "$run_dir/abandoned.json" ] && [ -f "$PIDFILE" ] && [ -f "$HALT" ]; then
    echo "ok: abandon refuses a live orphaned pid and keeps the marker and evidence"
  else
    echo "FAIL: abandon parked a phase with a live orphaned pid"; fail=1
  fi

  rm -f "$PIDFILE" "$HALT" "$run_dir/abandoned.json"
  DRIVER_STUB_BOARD_FENCE="$old_fence"
  export DRIVER_STUB_BOARD_FENCE
}
