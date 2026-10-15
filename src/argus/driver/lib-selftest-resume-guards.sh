#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: the guards a resume runs before it launches.
#
# The gate is what separates a resumable run from a damaged result, the ledger
# proof is what stops a resume from continuing a run whose step pins no longer
# describe the tree, and the board fence is what stops a second machine from
# owning the phase while this one works it. All three fail closed, so every case
# here asserts that a refusal launched nothing.

selftest_resume_gate_cases() {
  # A damaged run result is not the interrupted class. The interrupted class has
  # no run.json at all; a run.json present but unreadable must be refused by
  # name, because reading it as interrupted let a damaged result reach the
  # launch.
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  printf 'not json at all\n' >"$run_dir/run.json"
  printf '{"current": "fixture"}\n' >"$run_dir/resume.json"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'run.json is unreadable or damaged' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: a damaged run.json refuses even with a resume.json beside it"
  else
    echo "FAIL: a damaged run.json was accepted as the interrupted class"; fail=1
  fi
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

selftest_resume_ledger_cases() {
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
  # A repin that did not settle every pin on HEAD must refuse. The stub leaves the
  # ledger alone, so the settle check itself is what this case tests: trusting an
  # unsettled repin would continue a run whose pins no longer describe the tree.
  if [ -n "$head" ] \
      && git -C "$REPO" checkout -q --detach 2>/dev/null \
      && git -C "$REPO" commit -q --allow-empty -m 'resume fixture: a third commit' 2>/dev/null; then
    selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
    printf '{"steps": {"fixture": {"git_head": "%s"}}}\n' "$head" >"$run_dir/ledger.json"
    DRIVER_STUB_REPIN=unsettled
    export DRIVER_STUB_REPIN
    launches="$(selftest_resume_launches)"
    drive --self-test || true
    if grep -q 'did not settle every pin on HEAD' "$LOG" \
        && [ "$(selftest_resume_launches)" = "$launches" ]; then
      echo "ok: a repin that did not settle every pin refuses instead of resuming"
    else
      echo "FAIL: an unsettled auto-pin did not refuse"; fail=1
    fi
    unset DRIVER_STUB_REPIN
    git -C "$REPO" checkout -q - 2>/dev/null || true
  else
    echo "FAIL: the fixture could not move HEAD for the unsettled-pin case"; fail=1
  fi
  rm -f "$run_dir/ledger.json"

  # A ledger that cannot be read is not a ledger with no pins. The plan resumes on
  # no pins; reading a corrupt file the same way let a resume continue with the
  # pins unproven.
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  printf 'not json at all\n' >"$run_dir/ledger.json"
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q 'ledger at .* is unreadable' "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: an unreadable ledger refuses instead of resuming on no pins"
  else
    echo "FAIL: an unreadable ledger was read as no pins"; fail=1
  fi
  rm -f "$run_dir/ledger.json"
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
  unset DRIVER_STUB_BOARD_FENCE
  export DRIVER_STUB_BOARD_REPLY
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
}
