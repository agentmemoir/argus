#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: clearing a halt for a phase that never started.
#
# A halt before the phase started leaves a record and no run state, so `--resume`
# refuses it by design and `clear-halt` is the only way forward. The record is
# removed only for that case; a resumable phase keeps it, because removing it
# would silently lose the halt.

selftest_case_clear_halt() {
  local phase=100010 rel run_dir out launches
  local old_sleep="$DRIVER_STUB_SLEEP" old_fence="${DRIVER_STUB_BOARD_FENCE:-}"
  local old_coordination="${DRIVER_STUB_COORDINATION:-}" old_recover="$RECOVER_OWN_CLAIM"
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=0
  DRIVER_STUB_COORDINATION=fence
  RECOVER_OWN_CLAIM=1
  export DRIVER_STUB_RC DRIVER_STUB_SLEEP DRIVER_STUB_COORDINATION RECOVER_OWN_CLAIM
  wait_phase_window_gone
  mkdir -p "$run_dir"
  rm -f "$run_dir/run.json" "$run_dir/resume.json" "$run_dir/ledger.json" "$HALT"

  # A never-started phase: a record, no run.json and no resume.json. The tick
  # refuses the resume, which is why clearing the marker alone never moved the
  # queue.
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  launches="$(selftest_resume_launches)"
  local log_before
  log_before="$( [ -f "$LOG" ] && wc -l <"$LOG" || printf '0')"
  drive --self-test || true
  if tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null \
      | grep -q "resume refused: phase $phase: run.json is absent" \
      && [ "$(selftest_resume_launches)" = "$launches" ] \
      && [ -f "$HALT_RECORD" ]; then
    echo "ok: a phase with no run state refuses to resume and keeps its record"
  else
    echo "FAIL: a phase with no run state resumed or lost its record"; fail=1
  fi

  out="$(clear_halt_cmd)"
  if [ ! -f "$HALT_RECORD" ] && [ ! -f "$HALT" ] \
      && printf '%s' "$out" | grep -q "removed $HALT_RECORD (phase $phase never started)"; then
    echo "ok: clear-halt removes a never-started phase's record and names it"
  else
    echo "FAIL: clear-halt did not remove and name the never-started record"; fail=1
    { printf '      %s\n' "$out"; } || true
  fi
  launches="$(selftest_resume_launches)"
  log_before="$( [ -f "$LOG" ] && wc -l <"$LOG" || printf '0')"
  drive --self-test || true
  wait_phase_window_gone
  sleep 0.3
  if [ "$(selftest_resume_launches)" != "$launches" ] \
      && ! tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null \
        | grep -q "resume refused: phase $phase"; then
    echo "ok: the next tick starts the cleared phase instead of refusing"
  else
    echo "FAIL: the next tick did not start the cleared phase"; fail=1
  fi

  # A phase with a run result is resumable, so the record stays.
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  out="$(clear_halt_cmd)"
  if [ -f "$HALT_RECORD" ] && printf '%s' "$out" | grep -q "kept $HALT_RECORD: phase $phase has a run result"; then
    echo "ok: clear-halt keeps the record of a phase with run.json"
  else
    echo "FAIL: clear-halt removed the record of a phase with run.json"; fail=1
  fi

  # A phase with only resume state is resumable too.
  rm -f "$run_dir/run.json"
  printf '{"current": "fixture"}\n' >"$run_dir/resume.json"
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  out="$(clear_halt_cmd)"
  if [ -f "$HALT_RECORD" ] && printf '%s' "$out" | grep -q "kept $HALT_RECORD: phase $phase has a run result"; then
    echo "ok: clear-halt keeps the record of a phase with resume.json"
  else
    echo "FAIL: clear-halt removed the record of a phase with resume.json"; fail=1
  fi

  # Put back the state the other cases leave behind.
  rm -f "$run_dir/resume.json" "$HALT" "$HALT_RECORD"
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"
  DRIVER_STUB_SLEEP="$old_sleep"
  DRIVER_STUB_BOARD_FENCE="$old_fence"
  RECOVER_OWN_CLAIM="$old_recover"
  if [ -n "$old_coordination" ]; then
    DRIVER_STUB_COORDINATION="$old_coordination"
  else
    unset DRIVER_STUB_COORDINATION
  fi
  export DRIVER_STUB_SLEEP DRIVER_STUB_BOARD_FENCE RECOVER_OWN_CLAIM DRIVER_STUB_COORDINATION
}
