#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
# (scratch bisect touch)
# Self-test cases: the guards a resume runs before it launches.
#
# The gate names a terminal or damaged run and lets the two-tick repair run, and
# the board fence is what stops a second machine from owning the phase while
# this one works it. The fence fails closed; the gate never stops a launch.

selftest_resume_gate_cases() {
  # A damaged run result is named and passed to the repair, never used as a
  # reason to hold the line. The message still separates it from the interrupted
  # class, which has no run.json at all.
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  printf 'not json at all\n' >"$run_dir/run.json"
  printf '{"current": "fixture"}\n' >"$run_dir/resume.json"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'run.json is unreadable or damaged' "$LOG" \
      && grep -q 'continuing on its own branch for the repair' "$LOG" \
      && [ "$(selftest_resume_launches)" != "$launches" ]; then
    echo "ok: a damaged run.json is named and passed to the repair"
  else
    echo "FAIL: a damaged run.json stopped the resume instead of repairing it"; fail=1
  fi
  # The repair launched a phase, so let that window close before the next case.
  wait_phase_window_gone
  sleep 0.3
  rm -f "$run_dir/resume.json"
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"

  # A record whose fields have the wrong types is not a resume either: the
  # reader must refuse it. A numeric `state`, a string `rc`, and an empty
  # `source` are the shapes the schema refuses.
  printf '{"version": 1, "phase": "%s", "phase_file": "%s", "run_dir": "%s", "state": 5, "rc": "x", "source": "", "marker": "", "reservation_id": "", "generation": "", "recorded_at": ""}\n' \
    "$phase" "$rel" "$run_dir" >"$HALT_RECORD"
  rm -f "$HALT"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'is missing or malformed' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: a record with wrong field types is refused"
  else
    echo "FAIL: a record with wrong field types was acted on"; fail=1
  fi
  rm -f "$HALT" "$HALT_RECORD"
}

selftest_resume_fence_cases() {
  # A real board reply flows through the real parser: `reserve` prints
  # {"state": "OK", "reservation": {...}}. The nested id and generation must
  # reach the halt-clear line and the claim file. The record's fence is a fake,
  # so the real renew refuses and the fresh reservation (the stub reply) is
  # taken.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"
  DRIVER_STUB_BOARD_REPLY='{"state": "OK", "reservation": {"reservation_id": "rsv-real-shape", "generation": 42}}'
  # The reply remains real-shaped and passes through the parser; only the later
  # owner assertion is stubbed because the reservation itself was not written
  # to this fixture's board.
  DRIVER_STUB_BOARD_FENCE=assert
  export DRIVER_STUB_BOARD_REPLY DRIVER_STUB_BOARD_FENCE
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  wait_phase_window_gone
  sleep 0.3
  if grep -q "halt-clear: phase $phase resume launched (stage .*reservation rsv-real-shape gen 42)" "$LOG" \
      && grep -q 'rsv-real-shape' "$CLAIM" \
      && [ "$(selftest_resume_launches)" != "$launches" ]; then
    echo "ok: the real reserve reply is parsed into the fence"
  else
    echo "FAIL: the real reserve reply was not parsed"; fail=1
  fi
  unset DRIVER_STUB_BOARD_REPLY

  # The fence: the halted reservation is renewed when it can be, and when it
  # cannot, a free slot is taken. Both refusals stop the resume, because a launch
  # with no fence would let a second machine own the phase.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  DRIVER_STUB_BOARD_REPLY='{"state": "OK", "reservation": {"reservation_id": "rsv-resume-stub", "generation": 9}}'
  DRIVER_STUB_BOARD_FENCE=assert
  export DRIVER_STUB_BOARD_REPLY DRIVER_STUB_BOARD_FENCE
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
  unset DRIVER_STUB_BOARD_REPLY

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
}
