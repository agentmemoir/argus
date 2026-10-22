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
selftest_resume_record() {   # <phase> <rel> <run_dir> <state> <rc> [reservation_id] [generation]
  printf 'phase %s rc=%s state=%s at fixture\n' "$1" "$5" "$4" >"$HALT"
  # A halt leaves its claim beside the run, and the record keeps that fence so a
  # resume renews it instead of taking a new one. A case that made a real board
  # claim passes its pair, because record_claim reads this file back and the
  # board refuses a pair it did not issue.
  printf '%s\t%s\t%s\n' "$1" "${6:-rsv-fixture}" "${7:-7}" >"$CLAIM"
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
  local phase=100010 rel run_dir old_sleep old_fence old_coordination
  local launches head resume_out log_before advanced
  rel="$(resume_phase_file_of "$phase")" || rel=""
  run_dir="$(phase_run_dir "$phase" "$rel")"
  old_sleep="$DRIVER_STUB_SLEEP"
  old_fence="${DRIVER_STUB_BOARD_FENCE:-}"
  old_coordination="${DRIVER_STUB_COORDINATION:-}"
  DRIVER_STUB_SLEEP=0
  DRIVER_STUB_BOARD_FENCE=renew
  # The branch cut is the tick's own step and is not what these cases prove. The
  # stub keeps a case from cutting a real branch in the fixture.
  DRIVER_STUB_COORDINATION=resume
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

  # A record whose run is not blocked is a repair, not a stop. The gate names
  # the state and the launch proceeds, so glide can restore the step or re-run
  # it.
  selftest_resume_record "$phase" "$rel" "$run_dir" completed 1
  launches="$(selftest_resume_launches)"
  drive --self-test || true
  if grep -q "resume gate: phase $phase: the run is completed" "$LOG" \
      && grep -q 'continuing on its own branch for the repair' "$LOG" \
      && [ "$(selftest_resume_launches)" != "$launches" ]; then
    echo "ok: a completed record is named and passed to the repair"
  else
    echo "FAIL: a completed record stopped the resume instead of repairing it"; fail=1
    { head -12 "$LOG" 2>/dev/null | sed 's/^/      /'; } || true
  fi
  # The repair launched a phase, so let that window close before the next case:
  # a live window makes the tick skip, and the skip would read as a refusal.
  wait_phase_window_gone
  sleep 0.3

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
  # away. This is the interrupted class (no run record, only the resume state
  # beside it), because that is the case where the child would otherwise ask for
  # `--fresh`. The child is run directly, in an environment this case controls: a
  # tmux pane inherits the session's own environment, so the stub would reach it
  # there and this branch would never run.
  rm -f "$run_dir/run.json"
  printf '{"current": "fixture"}\n' >"$run_dir/resume.json"
  selftest_resume_record "$phase" "$rel" "$run_dir" "" 1
  ( unset DRIVER_STUB_RC; CLIO_RESUME=1 run_session "$phase" "$rel" ) \
    >/dev/null 2>&1 || true
  if grep -q "is resuming (prior state none); the run is not archived" "$LOG" \
      && ! grep -q 'restarting it with --fresh' "$LOG"; then
    echo "ok: a resume never restarts the run it continues with --fresh"
  else
    echo "FAIL: a resume restarted the run with --fresh"; fail=1
  fi
  rm -f "$run_dir/resume.json"
  printf '{"phase": "100010", "state": "blocked"}\n' >"$run_dir/run.json"

  # The gate and the board fence, in their own file: each is a guard the resume
  # runs before it launches.
  selftest_resume_gate_cases
  selftest_resume_fence_cases

  # `--resume N` names the phase directly, with no halt record at all. Only an
  # unnamed resume with nothing to resume may exit.
  rm -f "$HALT" "$HALT_RECORD" "$run_dir/ledger.json"
  printf '{"phase": "%s", "state": "blocked"}\n' "$phase" >"$run_dir/run.json"
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  launches="$(selftest_resume_launches)"
  log_before="$(wc -l <"$LOG" 2>/dev/null || printf '0')"
  exec 9>&- || true
  resume_out="$( ( resume_cmd "$phase" ) 2>&1 )" || true
  wait_phase_window_gone
  sleep 0.3
  if [ "$(selftest_resume_launches)" != "$launches" ] \
      && tail -n "+$((log_before + 1))" "$LOG" 2>/dev/null \
        | grep -q "halt-clear: phase $phase resume launched"; then
    echo "ok: --resume N resumes a named phase with no halt record"
  else
    echo "FAIL: --resume N refused a named phase with no halt record"; fail=1
    printf '      resume_cmd said: %s\n' "${resume_out:-<nothing>}"
  fi
  # `--resume N` is the operator's half of the same feature: it resumes while the
  # line stays halted, so the marker it found must still be there afterwards.
  DRIVER_STUB_BOARD_FENCE=renew
  export DRIVER_STUB_BOARD_FENCE
  selftest_resume_record "$phase" "$rel" "$run_dir" blocked 1
  printf 'halted for the resume case\n' >"$HALT"
  launches="$(selftest_resume_launches)"
  exec 9>&- || true
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
  # `--resume N` must not consume a record that names another phase: that halt
  # is still the queue's work. The named phase still launches.
  rm -f "$HALT"
  halt_record_write 100499 "$rel" "$run_dir" blocked 1 rc
  launches="$(selftest_resume_launches)"
  exec 9>&- || true
  resume_out="$( ( resume_cmd "$phase" ) 2>&1 )" || true
  wait_phase_window_gone
  sleep 0.3
  if [ "$(selftest_resume_launches)" != "$launches" ] && [ -f "$HALT_RECORD" ] \
      && grep -q '"phase": "100499"' "$HALT_RECORD"; then
    echo "ok: --resume N leaves another phase's record in place"
  else
    echo "FAIL: --resume N consumed another phase's record"; fail=1
    printf '      resume_cmd said: %s\n' "${resume_out:-<nothing>}"
  fi

  rm -f "$HALT" "$HALT_RECORD" "$run_dir/ledger.json"
  DRIVER_STUB_SLEEP="$old_sleep"
  DRIVER_STUB_BOARD_FENCE="$old_fence"
  DRIVER_STUB_COORDINATION="$old_coordination"
  export DRIVER_STUB_SLEEP DRIVER_STUB_BOARD_FENCE DRIVER_STUB_COORDINATION
}

# Claim one phase on a fixture's board and write the run state's
# `reservation.json`, which is the file every write path reads before its first
# write. The shell counterpart of the Python fixtures' `_reserve_phase`, and it
# calls that helper rather than repeating it: one reservation, made through the
# production store, on the board branch the fixture's own configuration names.
#
# A fixture that wants to drive a fenced write path without a tick to reserve
# first needs this. Without it the path reads no reservation, refuses with
# COORDINATION_UNAVAILABLE, and the case proves the refusal instead of the work
# it exists to prove.
#
#   $1  the root     $2  the phase
selftest_fixture_claim_phase() {
  "$PYTHON" - "$ARGUS" "$1" "$2" "$PHASE_MACHINE_ID" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
import _bootstrap  # noqa: F401 - register the source package
from argus import conventions

conventions.configure_from_cli(sys.argv[5:])
from argus.gitsync.selftest_fixture import _reserve_phase

_reserve_phase(
    sys.argv[2],
    int(sys.argv[3]),
    machine_id=sys.argv[4],
    branch=conventions.setting("board.branch"),
)
PY
}

selftest_case_group_resume() {
  # A resume has no selection to read the group from, and it still has to land on
  # the group's one branch: the tick's resume branch runs before the selector,
  # and `--resume` never selects at all. Without the group the branch step names
  # a branch from this member alone, so the member's receipt lands on a branch
  # none of the group's joins know and a second branch carrying the group's
  # commits is left on the remote.
  local group_root="$tmp/group-resume-root" group_base="$tmp/group-resume-base"
  local group_config="$tmp/group-resume-config.json"
  local group_branch="feature/group-cache-warm" member_branch="feature/cache-read"
  local rel="" run_dir="" head="" rc=0 rid="" gen=""
  local old_coordination="${DRIVER_STUB_COORDINATION:-}" old_sleep="$DRIVER_STUB_SLEEP"
  if ! selftest_group_begin group-resume "$group_root" "$group_base" "$group_config"; then
    echo "FAIL: the group-resume case could not build its own checkout"
    fail=1
    return 0
  fi
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=1
  # The branch cut is the step under test, so the real helper has to run here.
  unset DRIVER_STUB_COORDINATION
  export DRIVER_STUB_RC DRIVER_STUB_SLEEP

  # The member the case resumes holds a real claim before the branch is cut, the
  # way a member of a half-run group holds one: the group's earlier tick reserved
  # it. The branch helper proves that claim before it touches either checkout, so
  # a fixture with no reservation would exercise the refusal, and the branch it
  # never cut would leave every check below failing for the same wrong reason.
  if ! selftest_fixture_claim_phase "$group_root" 100030; then
    echo "FAIL: the group-resume case could not reserve phase 100030"
    fail=1
    # The rest of the suite runs in this process, so a case that cannot finish
    # still puts back the settings it repointed.
    DRIVER_STUB_SLEEP="$old_sleep"
    if [ -n "$old_coordination" ]; then
      DRIVER_STUB_COORDINATION="$old_coordination"
    else
      unset DRIVER_STUB_COORDINATION
    fi
    export DRIVER_STUB_SLEEP DRIVER_STUB_COORDINATION
    selftest_group_end
    return 0
  fi

  # The group's first member is waiting on its pull request, which is what makes
  # it an open predecessor for the member being resumed. The receipt carries no
  # head on purpose: the merge check refuses a receipt with nothing to verify
  # without asking GitHub, so the case stays off the network.
  mkdir -p "$WF/phase-100020"
  printf '{"version": 1, "phase": "100020", "state": "awaiting_review", "branch": "%s", "private_branch": "master", "integration_branch": "master"}\n' \
    "$group_branch" >"$WF/phase-100020/publication.json"

  # The group's one branch, cut the way the group's first tick cuts it, so the
  # resume starts where a member of a half-run group really starts.
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --mode branch --phase 100030 --phases phase-100030-cache-read \
    --phases-directory "$(phases_dir)" --group 100020 --group 100030 \
    >"$RUN_DIR/group-cut.json" 2>&1 || rc=$?
  head="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ "$rc" -eq 0 ] && [ "$head" = "$group_branch" ]; then
    echo "ok: the group-resume case cut the group's one branch first"
  else
    echo "FAIL: the group-resume case could not cut the group's branch (HEAD=$head)"; fail=1
    printf '      the branch helper said: %s\n' \
      "$(tr '\n' ' ' <"$RUN_DIR/group-cut.json" 2>/dev/null)"
  fi

  # The second member halts and the operator clears the marker, which is the
  # state the next tick resumes. The record keeps the fence a halt leaves.
  rel="$(resume_phase_file_of 100030)" || rel=""
  run_dir="$(phase_run_dir 100030 "$rel")"
  mkdir -p "$run_dir"
  printf '{"phase": "100030", "state": "blocked"}\n' >"$run_dir/run.json"
  # The halt record must name the same board-confirmed pair the branch fence
  # read, because record_claim reads this file back and the board refuses a
  # pair it did not issue.
  rid="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["reservation_id"])' "$run_dir/reservation.json")" || rid=""
  gen="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"])' "$run_dir/reservation.json")" || gen=""
  if [ -z "$rid" ] || [ -z "$gen" ]; then
    echo "FAIL: the group-resume case lost the reservation fence"
    fail=1
  else
    selftest_resume_record 100030 "$rel" "$run_dir" blocked 1 "$rid" "$gen"
  fi
  drive || true

  # The launch has to have happened, or the two checks below would pass on a
  # resume that refused before it cut anything.
  grep -q 'launched phase 100030' "$LOG" \
    && echo "ok: the group's second member resumed and launched" \
    || { echo "FAIL: the resumed group member did not launch"; fail=1; }
  head="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ "$head" = "$group_branch" ]; then
    echo "ok: a resumed group member stays on the group's one branch"
  else
    echo "FAIL: the resumed group member left the group's branch (HEAD=$head)"; fail=1
  fi
  if git -C "$REPO" rev-parse --verify --quiet "refs/heads/$member_branch" >/dev/null 2>&1 \
      || git -C "$REPO" rev-parse --verify --quiet "refs/remotes/origin/$member_branch" >/dev/null 2>&1; then
    echo "FAIL: the resume cut a branch of its own for the group member"; fail=1
  else
    echo "ok: the resume cut no branch of its own for the group member"
  fi

  # A declaration the resume cannot read is refused, not read as "runs alone": a
  # resume that cannot see the group would cut exactly the wrong branch. One
  # member naming a set the other does not is the malformed case the phases
  # reader refuses.
  wait_phase_window_gone
  cat >"$REPO/phases-data/plans/phase-100020-cache-warm.md" <<DOC
# 100020. Group member

- [ ] Work to do.

### Reservation Group

\`together: 100020\`
DOC
  git -C "$REPO/phases-data" add -A
  git -C "$REPO/phases-data" commit -q -m "phases: break the group declaration"
  launches="$(selftest_resume_launches)"
  printf '{"phase": "100030", "state": "blocked"}\n' >"$run_dir/run.json"
  selftest_resume_record 100030 "$rel" "$run_dir" blocked 1
  drive || true
  # One logged line has to carry all three facts: the refusal, the phases
  # reader's own words naming the document to fix, and the way out of the
  # refusal. A refusal that only says the group could not be read sends the
  # operator looking for the file.
  if grep -q "the reservation group of phase 100030 could not be read (.*phase-100020-cache-warm.md.*fix the group declaration, or abandon the phase" "$LOG" \
      && [ "$(selftest_resume_launches)" = "$launches" ]; then
    echo "ok: a resume refuses a group declaration it cannot read, and names the document to fix"
  else
    echo "FAIL: a resume read a group declaration it cannot read"; fail=1
  fi
  rm -f "$HALT"

  tmux kill-session -t "$SESSION" 2>/dev/null || true
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  DRIVER_STUB_SLEEP="$old_sleep"
  if [ -n "$old_coordination" ]; then
    DRIVER_STUB_COORDINATION="$old_coordination"
  else
    unset DRIVER_STUB_COORDINATION
  fi
  export DRIVER_STUB_SLEEP DRIVER_STUB_COORDINATION
  selftest_group_end
}
