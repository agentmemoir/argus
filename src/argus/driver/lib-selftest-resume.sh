#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: the halt record, the resume, and the abandon close-out.
#
# The fixture already holds a `blocked` run for phase 100010, which is the state
# a halt leaves and the only state a resume may continue. A case that needs
# another run state writes one, and a case that needs HEAD to have moved makes
# the move itself and undoes it.

# Write a halt record the way the driver writes one, then clear the marker, which
# is what an operator does before the next tick resumes. Using the driver's own
# writer keeps a case from spelling the schema out and from drifting from it.
selftest_resume_record() {   # <phase> <rel> <run_dir> <state> <rc>
  printf 'phase %s rc=%s state=%s at fixture\n' "$1" "$5" "$4" >"$HALT"
  # A halt leaves its claim beside the run, and the record keeps that fence so a
  # resume renews it instead of taking a new one.
  printf '%s\trsv-fixture\t7\n' "$1" >"$CLAIM"
  halt_record_write "$1" "$2" "$3" "$4" "$5" rc
  rm -f "$HALT"
}

# The number of launches the log records so far, so a case can prove a refusal
# launched nothing rather than only that it printed a line. An absent log counts
# as zero: the first case runs before the driver has written one, and an empty
# string would compare unequal to the `0` the same call returns a moment later.
selftest_resume_launches() {
  if [ -f "$LOG" ]; then
    grep -c 'launched phase' "$LOG" || true
  else
    printf '0'
  fi
}

selftest_case_resume() {
  local phase=100010 rel run_dir old_sleep old_fence old_repin old_coordination
  local launches head resume_out log_before advanced
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  old_sleep="$DRIVER_STUB_SLEEP"
  old_fence="${DRIVER_STUB_BOARD_FENCE:-}"
  old_repin="${DRIVER_STUB_REPIN:-}"
  old_coordination="${DRIVER_STUB_COORDINATION:-}"
  DRIVER_STUB_SLEEP=0
  DRIVER_STUB_BOARD_FENCE=renew
  # The branch cut is the tick's own step and is not what these cases prove. The
  # stub keeps a case from cutting a real branch in the fixture.
  DRIVER_STUB_COORDINATION=resume
  unset DRIVER_STUB_REPIN
  export DRIVER_STUB_SLEEP DRIVER_STUB_BOARD_FENCE DRIVER_STUB_COORDINATION
  # The previous case's phase window may still be closing, and a live window
  # makes the tick skip rather than resume. Wait for it before the first check.
  # The live run record the case needs, written rather than assumed. The fixture
  # starts phase 100010 as `blocked`, but the flow case ends a run as
  # `remedy_escalated` in this same run directory, and the child re-checks the
  # live state before it starts glide. A case that read the fixture's first value
  # would pass or fail according to which cases ran before it.
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"
  wait_phase_window_gone
  rm -f "$HALT" "$run_dir/ledger.json"

  # A record whose run is not blocked must not resume. This is the class the
  # queue used to advance past, so the refusal has to be visible and it must
  # launch nothing.
  selftest_resume_record "$phase" "$rel" "$run_dir" completed 1
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q "resume refused: phase $phase: the run is completed" "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ] \
      && [ -f "$HALT_RECORD" ]; then
    echo "ok: a completed record refuses to resume, launches nothing, and keeps the record"
  else
    echo "FAIL: a completed record did not refuse and keep the record"; fail=1
    { head -12 "$LOG" 2>/dev/null | sed 's/^/      /'; } || true
  fi

  # A malformed record is not a resume either: a reader that guessed at it would
  # launch whatever phase the guess landed on.
  printf 'not json at all\n' >"$HALT_RECORD"
  rm -f "$HALT"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'is missing or malformed' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ] \
      && [ -f "$HALT_RECORD" ]; then
    echo "ok: a malformed record refuses to resume, launches nothing, and keeps the record"
  else
    echo "FAIL: a malformed record did not refuse and keep the record"; fail=1
  fi

  # The record a halt actually leaves: the run is blocked, so the resume proceeds
  # and consumes the record. A ledger with no pins is the case where nothing has
  # to be proven about the tree.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  wait_phase_window_gone
  sleep 0.3
  if grep -q "halt-clear: phase $phase resume launched" "$LOG" \
      && [ "$(selftest_resume_launches)" != "$launches" ] \
      && [ ! -f "$HALT_RECORD" ] && [ ! -f "$HALT" ]; then
    echo "ok: a blocked record resumes, launches, and is consumed"
  else
    echo "FAIL: a blocked record did not resume and consume its record"; fail=1
    printf '      DRIVE_RC=[%s] marker=[%s] record=[%s]\n' "${_DRIVE_RC:-}" \
      "$([ -f "$HALT" ] && echo present || echo absent)" \
      "$([ -f "$HALT_RECORD" ] && echo present || echo absent)"
    { grep -n 'resume\|halt\|window is running\|orphan' "$LOG" 2>/dev/null | tail -6 | sed 's/^/      /'; } || true
  fi
  # The plan's own acceptance measure, asserted rather than described: a cleared
  # marker resumes the recorded phase and reserves no other phase. This is the
  # check that would have caught the 2026-10-14 advance, when a cleared marker
  # let the queue launch 101040 after 101032 had halted.
  log_before="$(wc -l <"$LOG" 2>/dev/null || printf '0')"
  drive --self-test || true
  advanced="$(tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null | grep -c 'reserved phase' || true)"
  if [ "${advanced:-0}" = 0 ]; then
    echo "ok: the tick resumed the recorded phase and reserved no other phase"
  else
    echo "FAIL: the tick reserved another phase instead of resuming the halted one"; fail=1
    { tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null | sed 's/^/      /'; } || true
  fi


  # The run is not archived: a resume continues the state `--fresh` would throw
  # away. This is the interrupted class (an unreadable run record with only the
  # resume state beside it), because that is the case where the child would
  # otherwise ask for `--fresh`. The child is run directly, in an environment
  # this case controls: a tmux pane inherits the session's own environment, so
  # the stub would reach it there and this branch would never run.
  printf 'not json at all\n' >"$run_dir/run.json"
  printf '{"current": "fixture"}\n' >"$run_dir/resume.json"
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  ( unset DRIVER_STUB_RC; CLIO_RESUME=1 run_session "$phase" "$rel" ) \
    >/dev/null 2>&1 || true
  if grep -q "is resuming (prior state unreadable); the run is not archived" "$LOG" \
      && ! grep -q 'restarting it with --fresh' "$LOG"; then
    echo "ok: a resume never restarts the run it continues with --fresh"
  else
    echo "FAIL: a resume restarted the run with --fresh"; fail=1
  fi
  rm -f "$run_dir/resume.json"
  printf '{"phase": "100010", "state": "blocked"}\n' >"$run_dir/run.json"

  # The ledger proof and the board fence, in their own file: each is a guard the
  # resume runs before it launches, and each has its own cases.
  selftest_resume_ledger_cases
  selftest_resume_fence_cases

  # `--resume N` must not read another phase's records. The record here names a
  # different phase and carries a run directory of its own, and that directory's
  # ledger pins a commit HEAD does not descend from. Read for the phase being
  # resumed the resume proceeds; read for the phase the record names it refuses.
  # The case proves which directory the proof was taken from.
  local other="$RUN_DIR/other-phase-run" other_rel
  other_rel="$(resume_phase_file_of "$phase")" || other_rel="$rel"
  mkdir -p "$other"
  printf '{"steps": {"fixture": {"git_head": "%s"}}}\n' \
    "0000000000000000000000000000000000000000" >"$other/ledger.json"
  halt_record_write 100499 "$other_rel" "$other" blocked 1 rc
  rm -f "$HALT" "$run_dir/ledger.json"
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  launches="$(selftest_resume_launches)"
  # Only the lines this attempt adds: the log is one file across the whole suite,
  # so a stale refusal from an earlier case would satisfy a bare grep.
  log_before="$(wc -l <"$LOG" 2>/dev/null || printf '0')"
  resume_out="$( ( resume_cmd "$phase" ) 2>&1 )" || true
  wait_phase_window_gone
  sleep 0.3
  if [ "$(selftest_resume_launches)" != "$launches" ] \
      && tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null \
        | grep -q "halt-clear: phase $phase resume launched" \
      && ! tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null \
        | grep -q 'HEAD does not descend from'; then
    echo "ok: --resume N proves the named phase's ledger, not the record's"
  else
    echo "FAIL: --resume N read the record's run directory for the named phase"; fail=1
    printf '      resume_cmd said: %s\n' "${resume_out:-<nothing>}"
  fi
  rm -rf "$other"
  # `--resume N` is the operator's half of the same feature: it resumes while the
  # line stays halted, so the marker it found must still be there afterwards.
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  printf 'halted for the resume case\n' >"$HALT"
  launches="$(selftest_resume_launches)"
  # A subshell: `resume_cmd` exits on refusal, and that exit must not end the
  # suite with cases still to run. Its output is kept so a failure can say why.
  resume_out="$( ( resume_cmd "$phase" ) 2>&1 )" || true
  wait_phase_window_gone
  sleep 0.3
  if [ "$(selftest_resume_launches)" != "$launches" ] && [ -f "$HALT" ] \
      && [ ! -f "$HALT_RECORD" ]; then
    echo "ok: --resume N launches while the line stays halted"
  else
    echo "FAIL: --resume N did not launch, or it cleared the marker"; fail=1
    printf '      resume_cmd said: %s\n' "${resume_out:-<nothing>}"
    printf '      DRIVE_RC=[%s]\n' "${_DRIVE_RC:-}"
    printf '      marker=[%s]\n' "$(tr '\n' ' ' <"$HALT" 2>/dev/null || echo absent)"
  fi

  rm -f "$HALT" "$run_dir/ledger.json"
  DRIVER_STUB_SLEEP="$old_sleep"
  DRIVER_STUB_BOARD_FENCE="$old_fence"
  DRIVER_STUB_COORDINATION="$old_coordination"
  if [ -n "$old_repin" ]; then DRIVER_STUB_REPIN="$old_repin"; else unset DRIVER_STUB_REPIN; fi
  export DRIVER_STUB_SLEEP DRIVER_STUB_BOARD_FENCE DRIVER_STUB_COORDINATION
}
