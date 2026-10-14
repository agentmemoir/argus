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
  local launches head resume_out
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

  # A pin HEAD does not descend from must fail closed and name the command a
  # person has to run: repinning it silently would rewrite a run whose outputs no
  # longer match the tree.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  printf '{"steps": {"fixture": {"git_head": "%s"}}}\n' \
    "0000000000000000000000000000000000000000" >"$run_dir/ledger.json"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'HEAD does not descend from' "$LOG" \
      && grep -q -- '--repin-head --repin-reason' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: a pin HEAD does not descend from fails closed and names the command"
  else
    echo "FAIL: a pin HEAD does not descend from was accepted"; fail=1
  fi

  # An ancestor pin only moved forward, so the resume takes the auto-pin path
  # once and continues. The extra commit is made on a detached HEAD and undone,
  # so the fixture's own branch is left where the next case expects it.
  head="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)"
  if [ -n "$head" ] \
      && git -C "$REPO" checkout -q --detach 2>/dev/null \
      && git -C "$REPO" commit -q --allow-empty -m 'resume fixture: a second commit' 2>/dev/null; then
    selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
    printf '{"steps": {"fixture": {"git_head": "%s"}}}\n' "$head" >"$run_dir/ledger.json"
    DRIVER_STUB_REPIN=ok
    export DRIVER_STUB_REPIN
    launches="$(selftest_resume_launches)"
    drive --self-test || true
    wait_phase_window_gone
    sleep 0.3
    if grep -q "resume auto-pin: (stub) would repin from \[$head\] to" "$LOG" \
        && [ "$(selftest_resume_launches)" != "$launches" ]; then
      echo "ok: an ancestor pin takes the auto-pin path once and resumes"
    else
      echo "FAIL: an ancestor pin did not take the auto-pin path"; fail=1
    fi
    unset DRIVER_STUB_REPIN
    git -C "$REPO" checkout -q - 2>/dev/null || true
  else
    echo "FAIL: the fixture could not move HEAD for the ancestor-pin case"; fail=1
  fi
  rm -f "$run_dir/ledger.json"

  # The fence: the halted reservation is renewed when it can be, and when it
  # cannot, a free slot is taken. Both refusals stop the resume, because a launch
  # with no fence would let a second machine own the phase.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  DRIVER_STUB_BOARD_FENCE=reserve
  export DRIVER_STUB_BOARD_FENCE
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  wait_phase_window_gone
  sleep 0.3
  if grep -q "halt-clear: phase $phase resume launched (stage .*reservation rsv-resume-stub gen 9)" "$LOG" \
      && [ "$(selftest_resume_launches)" != "$launches" ]; then
    echo "ok: a fence that cannot be renewed falls back to a fresh reservation"
  else
    echo "FAIL: a renew failure did not fall back to a fresh reservation"; fail=1
  fi

  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  DRIVER_STUB_BOARD_FENCE=fail
  export DRIVER_STUB_BOARD_FENCE
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'refused both a renewal and a fresh reservation' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: no fence at all stops the resume"
  else
    echo "FAIL: a resume launched with no fence"; fail=1
  fi

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

selftest_case_abandon() {
  local phase=100010 rel run_dir old_fence
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  old_fence="${DRIVER_STUB_BOARD_FENCE:-}"
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  wait_phase_window_gone
  rm -f "$HALT" "$HALT_RECORD" "$CLAIM" "$run_dir/abandoned.json"

  # A phase with no run directory is nothing to abandon. Parking it would hide
  # work the queue never did and would never retry. A subshell, because
  # `abandon_cmd` exits on refusal and that must not end the suite.
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
  rm -f "$run_dir/abandoned.json"
  DRIVER_STUB_BOARD_FENCE="$old_fence"
  export DRIVER_STUB_BOARD_FENCE
}
