#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: halt, escalation, terminal state, signal stop, and coordination failure.
#
selftest_case_flow() {
  # Exhausting the remedy rounds is not a verdict: a run that ends
  # remedy_escalated records its open findings, releases the claim, and writes
  # no halt marker, so the next tick runs the next phase. This exercises the
  # real handler, not a stubbed exit code.
  rm -f "$HALT"
  escalate_phase=100010
  escalate_dir="$WF/phase-$(printf '%06d' "$escalate_phase")"
  mkdir -p "$escalate_dir"
  printf '{"state":"remedy_escalated","events":[{"step":"approver","signal":"REMEDY_REJECTED: F-01 abc12345"}]}\n' \
    >"$escalate_dir/run.json"
  handle_escalation "$escalate_phase" "phase-$escalate_phase-escalation.md"
  [ -f "$escalate_dir/remedy-escalated.json" ] && [ ! -f "$HALT" ] \
    && echo "ok: escalation recorded without a halt marker" \
    || { echo "FAIL: escalation record or halt state wrong"; fail=1; }
  grep -q "exhausted its remedy rounds" "$LOG" \
    && echo "ok: escalation notification logged" \
    || { echo "FAIL: escalation notification missing"; fail=1; }
  rm -f "$HALT"

  # A terminal run the selector hands back must be restarted, not wedged: glide
  # refuses to rerun a finished run without --fresh, so the driver reads the
  # prior state and asks for a fresh start. A blocked run is resumable, and only
  # an absent marker has nothing to archive.
  terminal_probe="$RUN_DIR/terminal-state-probe.json"
  printf '{"state":"rejected"}\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "rejected" ] \
    && echo "ok: a rejected run is read as terminal and asks for --fresh" \
    || { echo "FAIL: a rejected run did not ask for --fresh"; fail=1; }
  printf '{"state":"blocked"}\n' >"$terminal_probe"
  [ -z "$(terminal_state_of_file "$terminal_probe")" ] \
    && echo "ok: a blocked run is left to resume, not restarted" \
    || { echo "FAIL: a blocked run was restarted"; fail=1; }
  printf 'not json\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "unreadable" ] \
    && echo "ok: an unreadable run marker still asks for --fresh" \
    || { echo "FAIL: an unreadable run marker did not ask for --fresh"; fail=1; }
  printf '{"other": 1}\n' >"$terminal_probe"
  [ "$(terminal_state_of_file "$terminal_probe")" = "unreadable" ] \
    && echo "ok: a marker with no state still asks for --fresh" \
    || { echo "FAIL: a marker with no state did not ask for --fresh"; fail=1; }
  rm -f "$terminal_probe"
  [ -z "$(terminal_state_of_file "$terminal_probe")" ] \
    && echo "ok: a phase with no run marker asks for no --fresh" \
    || { echo "FAIL: a missing marker asked for --fresh"; fail=1; }

  # Signal stop: Ctrl-C reaches the trap, which halts and notifies. This is the
  # case that proves Ctrl-C is aimed at the phase window: the idle window is
  # still there, and a session-scoped send-keys would have hit it instead.
  rm -f "$HALT"
  local stop_wait="$tmp/stop-wait" stop_ready="$tmp/stop-ready" stop_waited=0
  rm -f "$stop_wait" "$stop_ready"
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=20
  DRIVER_STUB_WAIT_FILE="$stop_wait"; DRIVER_STUB_READY_FILE="$stop_ready"
  export DRIVER_STUB_RC DRIVER_STUB_SLEEP DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  drive --self-test || true
  while [ ! -e "$stop_ready" ] && [ "$stop_waited" -lt 200 ]; do
    sleep 0.1
    stop_waited=$((stop_waited + 1))
  done
  [ -e "$stop_ready" ] \
    && echo "ok: stop reached the live phase stub" \
    || { echo "FAIL: stop stub never started"; fail=1; }
  stop || true
  : >"$stop_wait"
  wait_phase_window_gone
  [ -f "$HALT" ] \
    && echo "ok: stop wrote halt marker" || { echo "FAIL: stop marker missing"; fail=1; }
  grep -q "Phase .* stopped by signal\." "$LOG" \
    && echo "ok: stop notification logged" || { echo "FAIL: stop notification missing"; fail=1; }
  tmux_session_exists \
    && echo "ok: session survives an operator stop" \
    || { echo "FAIL: session died on operator stop"; fail=1; }

  # The next case is about the real path refusing, so the stub comes off first: it
  # runs a tick with no stub precisely so the coordination sync is attempted, and a
  # stub would make the case pass without reaching the refusal it is written for.
  # The recovery flag goes back with it, so the cases after this one start from the
  # state they would have started from before the stub was introduced.
  unset DRIVER_STUB_COORDINATION
  unset DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  RECOVER_OWN_CLAIM=0; export RECOVER_OWN_CLAIM
  rm -f "$stop_wait" "$stop_ready"

  # Coordination failure: the normal driver path must refuse before tmux launch.
  # Reservable but unsyncable: a real fixture board so the tick reaches the
  # sync gate, with the public checkout's remote unreachable so --mode start
  # fails there.
  # Selector states for a fixture_root: the root reserves 100010 under the
  # self-test machine id (argus-selftest), so the next tick re-encounters its
  # own claim as WAIT_FOR_CLAIM rather than a fresh RESERVED; the sync gate
  # runs before the state dispatch, so it still refuses with the asserted
  # line. A bare extra phase would not give RESERVED on its own while 100010
  # stays held.
  # Repo-internal pattern, same as the dead-branch case below.
  old_repo="$REPO"; old_board_dir="$BOARD_DIR"; old_wf="$WF"
  old_argus="$ARGUS"; old_notifier="$NOTIFIER"
  old_run_dir="$RUN_DIR"; old_lock="$LOCK"; old_halt="$HALT"; old_stall="$STALL"
  old_current="$CURRENT"; old_pid="$PIDFILE"; old_notify_broken="$NOTIFY_BROKEN"
  old_log="$LOG"; old_session="$SESSION"
  coord_root="$tmp/coordination-failure-root"
  coord_config="$tmp/coordination-failure-config.json"
  if ! selftest_fixture_root "$coord_root" "$tmp/coordination-failure-base"; then
    echo "FAIL: the coordination-failure fixture could not be built, so the refusal was not tested"
    fail=1
  else
    REPO="$coord_root"
    selftest_write_config "$coord_root" "$coord_config" "$BOARD_DIR"
    load_fixture_config "$coord_config"
    WORK_SOURCE_FLAGS=(--source phases)
    ARGUS="$coord_root/$BOARD_DIR/argus"; NOTIFIER="$ARGUS/notify/notifications.py"
    set_config_args
    RUN_DIR="$WF/$DRIVER_STATE"
    mkdir -p "$RUN_DIR"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    SESSION="ar-driver-coord-failure-$$"
    # The state the tick sees, read without reserving.
    coord_state="$(coordination_select --read-only 2>/dev/null | selection_value state || true)"
    # A sync that fails is a repair: the tick logs it, keeps the claim, and
    # continues. The checkout has to reach the gate, so its remote is pointed
    # at a path that is not there and its .git is left alone. A missing .git is
    # a narrow own-state stop one step earlier, covered by the lost-repo case
    # and the git ceiling.
    git -C "$coord_root" remote set-url origin "$tmp/coordination-failure-remote-gone.git"
    drive || true
    coord_sync_refused=0
    grep -q "coordination checkout sync failed; keeping the claim" "$LOG" && coord_sync_refused=1
    if [ "$coord_sync_refused" = 1 ]; then
      echo "ok: a coordination sync failure is a repair that keeps the claim"
    else
      coord_sync_hint="$( { head -c 300 "$RUN_DIR/coordination-sync.json" 2>/dev/null || true; } | tr '\n' ' ' )"
      echo "FAIL: coordination failure did not report the repair (sync_refused=$coord_sync_refused selection_state=${coord_state:-unknown} sync=$coord_sync_hint)"; fail=1
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
  fi
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  load_fixture_config "$selftest_config"
  RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
  CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
  LOG="$old_log"; SESSION="$old_session"

  # A merged phase whose branch was deleted on the remote, with the checkout
  # still sitting on it. This is the stall: the sync gate refused because the
  # branch had no upstream left, and both steps that would have moved the
  # checkout back ran after that refusal. The fixture is asserted to have the
  # shape before the tick runs, because a fixture that failed to build the dead
  # branch would make every assertion below pass for the wrong reason — and the
  # case before this one proved that a green check can come from a fixture that
  # is not the state it claims to be.
  dead_root="$tmp/dead-branch-root"
  dead_run="$tmp/dead-branch-run"
  if selftest_fixture_root "$dead_root" "$tmp/dead-branch-base" \
      && selftest_fixture_strand_on_published_branch "$dead_root" \
          "$tmp/dead-branch-base" "feature/dead-branch-fixture"; then
    REPO="$dead_root"; BOARD_DIR=board-data
    ARGUS_CONFIG_FILE="$tmp/dead-branch-config.jsonc"
    selftest_write_config "$dead_root" "$ARGUS_CONFIG_FILE" "$BOARD_DIR"
    load_fixture_config "$ARGUS_CONFIG_FILE"
    WORK_SOURCE_FLAGS=(--source phases)
    ARGUS="$dead_root/$BOARD_DIR/argus"
    NOTIFIER="$ARGUS/notify/notifications.py"
    RUN_DIR="$WF/$DRIVER_STATE"; mkdir -p "$RUN_DIR"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    SESSION="ar-driver-dead-branch-$$"
    # A configuration file for this sub-fixture, so the child tick it launches
    # reads the dead-branch checkout rather than the suite's.

    # What the tick is about to find, read from the fixture rather than assumed.
    # All three halves matter, because the reported stall was the shape where all
    # three held: the checkout on the phase branch, the remote copy deleted, and
    # the local tracking ref still present so the sync reached its own fetch
    # rather than stopping at "no upstream".
    dead_branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
    dead_remote="$(git -C "$REPO" ls-remote --heads origin \
      'refs/heads/feature/dead-branch-fixture' | wc -l | tr -d ' ')"
    dead_tracking=gone
    git -C "$REPO" rev-parse --verify --quiet \
      origin/feature/dead-branch-fixture >/dev/null 2>&1 || dead_tracking=absent
    if [ "$dead_branch" = "feature/dead-branch-fixture" ] \
        && [ "$dead_remote" = 0 ] && [ "$dead_tracking" = gone ]; then
      echo "ok: the fixture has a phase branch whose remote copy is deleted"
    else
      echo "FAIL: the dead-branch fixture is not stranded (branch '$dead_branch', remote copy count $dead_remote, tracking ref $dead_tracking)"
      fail=1
    fi

    rm -f "$HALT" "$RUN_DIR/coordination-outage"
    rm -f "$RUN_DIR/coordination-outage-first-seen"
    # A plain tick, not `--self-test`. That flag routes the tick down the stubbed
    # selection, which is the path where the sync gate is skipped altogether — so
    # a case written against it would pass with the gate in either order, and
    # would have passed before the fix too. This is the whole case: the gate is
    # the code under test, so the case has to reach it.
    dead_rc=0
    drive >/dev/null 2>&1 || dead_rc=$?
    dead_now="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    # The line carried on, rather than merely moving the branch. Asserting the
    # branch alone would pass on a tick that tidied and then refused for another
    # reason, which is the shape of a partial fix; and asserting a launch alone
    # would pass on a tick that launched without recovering, which would leave
    # the next phase built on a dead branch. Both are checked.
    dead_refusals="$(grep -c 'coordination checkout sync failed\|could not return a published phase' "$LOG" || true)"
    dead_launched="$(grep -c 'launched phase' "$LOG" || true)"
    # `|| true` on the pipeline, and not only on each stage: a `grep` that matches
    # nothing exits non-zero, `pipefail` carries that out of the pipeline, and the
    # case aborts on the very tick the fix is supposed to have changed. The run
    # would then report the fixture as untested rather than as unrecovered.
    dead_next_phase="$( { grep -m1 -o 'launched phase [0-9]*' "$LOG" || true; } | head -1 | tr -d ' ')"
    if [ "$dead_refusals" = 0 ] && [ "$dead_launched" -gt 0 ] \
        && [ -n "$dead_next_phase" ] \
        && [ "$dead_now" != "feature/dead-branch-fixture" ]; then
      echo "ok: a merged phase whose branch was deleted no longer stops the line (exit $dead_rc, on '$dead_now', '$dead_next_phase' launched)"
    else
      echo "FAIL: the dead branch was not recovered (exit $dead_rc, now on '$dead_now', refusals $dead_refusals, launches $dead_launched, next phase '$dead_next_phase')"
      fail=1
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
  else
    echo "FAIL: the dead-branch fixture could not be built, so the stall was not tested"
    fail=1
  fi
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  load_fixture_config "$selftest_config"
  RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
  CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
  LOG="$old_log"; SESSION="$old_session"

}

selftest_case_release() {
  # A release is the opposite of a stop: the run is stopped on purpose and the
  # group goes back to the queue, so nothing may write the halt marker, send a
  # halt notification, or trigger the stall or orphan check. The parent writes
  # the release marker before it interrupts the phase window; the child's signal
  # trap reads it. This exercises that handshake on a live stub run.
  rm -f "$HALT" "$RELEASE" "$CLAIM" "$RUN_DIR/stopped-claim"
  local release_wait="$tmp/release-wait" release_ready="$tmp/release-ready" release_rc=0
  rm -f "$release_wait" "$release_ready"
  local old_stub_sleep="${DRIVER_STUB_SLEEP:-}"
  DRIVER_STUB_RC=0
  DRIVER_STUB_WAIT_FILE="$release_wait"
  DRIVER_STUB_READY_FILE="$release_ready"
  DRIVER_STUB_COORDINATION=fence
  RECOVER_OWN_CLAIM=1
  export DRIVER_STUB_RC DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  export DRIVER_STUB_COORDINATION RECOVER_OWN_CLAIM
  drive --self-test || true
  local waited=0
  while [ ! -e "$release_ready" ] && [ "$waited" -lt 200 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if [ -e "$release_ready" ]; then
    echo "ok: the release case has a live stubbed phase to release"
  else
    echo "FAIL: the release stub never started, so the release case could not run"
    fail=1
  fi
  [ -s "$CLAIM" ] \
    && echo "ok: the tick recorded the claim fence a release needs" \
    || { echo "FAIL: the tick recorded no claim fence"; fail=1; }
  # Plant the manual-stop hold a release must clear, so the assertion after the
  # release has something to catch when the clear is missing.
  printf '%s\n' "$(cut -f1 "$CLAIM")" >"$RUN_DIR/stopped-claim"
  local before_stopped before_stalled before_orphan
  before_stopped="$(grep -c 'stopped by signal\|needs attention' "$LOG" || true)"
  before_stalled="$(grep -c 'looks stalled\|stalled:' "$LOG" || true)"
  before_orphan="$(grep -c 'orphaned run detected' "$LOG" || true)"
  release || release_rc=$?
  # Let any straggler pane finish if the interrupt did not reach it, so the
  # case cannot hang the suite.
  : >"$release_wait"
  sleep 0.3
  if [ -f "$RUN_DIR/stopped-claim" ]; then
    echo "FAIL: release left the manual-stop hold behind"
    fail=1
  else
    echo "ok: release cleared the manual-stop hold"
  fi
  if [ ! -f "$HALT" ]; then
    echo "ok: release did not write the halt marker"
  else
    echo "FAIL: release wrote the halt marker"
    fail=1
  fi
  if [ ! -f "$CURRENT" ] && [ ! -f "$PIDFILE" ]; then
    echo "ok: release cleared the current-phase and pid files"
  else
    echo "FAIL: release left the current-phase or pid file behind"
    fail=1
  fi
  # The claim fence is cleared once the board write succeeds, and kept when it
  # fails so a retry can finish the release. Both are correct; assert the shape.
  if [ -f "$CLAIM" ]; then
    echo "ok: a failed board write keeps the claim fence so release can be retried"
  else
    echo "ok: a completed release cleared the claim fence"
  fi
  if tmux_window_exists; then
    echo "FAIL: release left the phase window open"
    fail=1
  else
    echo "ok: release closed the phase window"
  fi
  if [ "$(grep -c 'stopped by signal\|needs attention' "$LOG" || true)" = "$before_stopped" ] \
      && [ "$(grep -c 'looks stalled\|stalled:' "$LOG" || true)" = "$before_stalled" ] \
      && [ "$(grep -c 'orphaned run detected' "$LOG" || true)" = "$before_orphan" ]; then
    echo "ok: release sent no halt notification and triggered no stall or orphan check"
  else
    echo "FAIL: release sent a halt notification or triggered a stall or orphan check"
    fail=1
  fi
  grep -q "release: sent Ctrl-C" "$LOG" \
    && echo "ok: release interrupted the phase window" \
    || { echo "FAIL: release did not interrupt the phase window"; fail=1; }
  [ "$release_rc" = 0 ] \
    && echo "ok: release returns success and does not exit through the error path" \
    || { echo "FAIL: release returned $release_rc"; fail=1; }
  unset DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE DRIVER_STUB_COORDINATION
  RECOVER_OWN_CLAIM=0; export RECOVER_OWN_CLAIM
  DRIVER_STUB_SLEEP="$old_stub_sleep"; export DRIVER_STUB_SLEEP
  rm -f "$release_wait" "$release_ready" "$RELEASE" "$CLAIM" "$HALT"
}

selftest_case_git_ceiling() {
  # A fixture that has lost its repository has to refuse, not resolve to a
  # repository above the fixture tree. The tree is inside the checkout under
  # test on a host whose temporary storage is `noexec`, so a walk-up reaches
  # the checkout the tick is running against; the ceiling driver_self_test
  # exports at the fixture root is what stops it.
  #
  # The case proves its own precondition instead of assuming the layout. The
  # same path is asked again with the ceiling off, and only when that resolves
  # upward is there something for a walk-up to reach: outside that layout the
  # check would pass with or without the bound, so it says so instead of
  # claiming one.
  local ceiling_root="$tmp/git-ceiling-root" above ceiling_out session_ceiling probe_session
  mkdir -p "$ceiling_root"
  if ! above="$(env -u GIT_CEILING_DIRECTORIES git -C "$ceiling_root" rev-parse --show-toplevel 2>/dev/null)"; then
    echo "skip: no repository encloses the fixture tree here, so the ceiling cannot bind (the CI layout, under the checkout, is the one it exists for)"
    return 0
  fi
  if ceiling_out="$(git -C "$ceiling_root" rev-parse --is-inside-work-tree 2>&1)"; then
    echo "FAIL: a fixture without its own repository resolved to $ceiling_out above the fixture tree at $above"; fail=1
  else
    echo "ok: a fixture without its own repository refuses git discovery (without the ceiling it resolved to $above)"
  fi
  # The tick runs inside tmux, and a pane takes its environment from the tmux
  # server, not from this shell — while the session the driver uses may not
  # exist yet when this case runs. So the probe starts a throwaway session from
  # this environment and reads the server's global environment, which is what
  # every pane inherits; it is killed again at once. The suite owns the server
  # this may start (TMUX is dropped before any tmux command), so a value found
  # here is the value a tick started by this suite would hand its panes.
  probe_session="git-ceiling-tmux-probe-$$"
  tmux new-session -d -s "$probe_session" 'sleep 5' 2>/dev/null || true
  session_ceiling="$(tmux show-environment -g GIT_CEILING_DIRECTORIES 2>/dev/null || true)"
  tmux kill-session -t "$probe_session" 2>/dev/null || true
  if [ "$session_ceiling" = "GIT_CEILING_DIRECTORIES=$tmp" ]; then
    echo "ok: a tmux server started from this environment carries the ceiling to its panes"
  else
    echo "FAIL: the tmux server does not carry the ceiling (got '${session_ceiling:-nothing}')"; fail=1
  fi
}

selftest_case_lost_repo() {
  # The ticket's own trigger, end to end: the fixture checkout's .git is gone
  # and the real tick runs inside tmux. With the ceiling, no git command the
  # tick makes can resolve the fixture root at all, so the launch is refused
  # before a phase starts; without one, `git -C` walks up to the repository
  # that holds the fixture tree, and the tick operates on that checkout
  # instead of failing — the incident #116 records.
  #
  # The tick is the suite's own session, named by SESSION_NAME, so the checks
  # below look at the session it really ran in. The repository a walk-up would
  # reach is read with the ceiling off, before the tick, because that is the
  # checkout the hazard moves and it has to be the same one afterwards.
  local lost_root="$tmp/git-ceiling-lost-root" lost_config="$tmp/git-ceiling-lost-config.json"
  local old_repo="$REPO" old_board_dir="$BOARD_DIR" old_wf="$WF" old_argus="$ARGUS"
  local old_notifier="$NOTIFIER" old_run_dir="$RUN_DIR" old_lock="$LOCK" old_halt="$HALT"
  local old_stall="$STALL" old_current="$CURRENT" old_pid="$PIDFILE"
  local old_notify_broken="$NOTIFY_BROKEN" old_log="$LOG"
  local lost_rc=0 outer_head="" outer_after="" lost_refused=0 lost_gate=0 lost_no_window=0
  outer_head="$(env -u GIT_CEILING_DIRECTORIES git -C "$tmp" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$outer_head" ]; then
    echo "skip: no repository encloses the fixture tree here, so a walk-up had nothing to reach (the CI layout, under the checkout, is the one this exists for)"
    return 0
  fi
  if ! selftest_fixture_root "$lost_root" "$tmp/git-ceiling-lost-base"; then
    echo "FAIL: the lost-repository fixture could not be built, so the tick's refusal was not tested"
    fail=1
  else
    REPO="$lost_root"
    selftest_write_config "$lost_root" "$lost_config" "$BOARD_DIR"
    load_fixture_config "$lost_config"
    WORK_SOURCE_FLAGS=(--source phases)
    ARGUS="$lost_root/$BOARD_DIR/argus"; NOTIFIER="$ARGUS/notify/notifications.py"
    set_config_args
    RUN_DIR="$WF/$DRIVER_STATE"
    mkdir -p "$RUN_DIR"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    rm -rf "$lost_root/.git"
    drive || lost_rc=$?
    # The refusal has to be the first git step's, and the absence of the later
    # one matters as much: with the ceiling the fixture root cannot resolve at
    # all, so tidy refuses to return the published checkout; without it the
    # walk-up reaches the enclosing checkout, tidy finds no receipt there and
    # leaves it alone, and the tick stops at the sync gate instead. Both stop a
    # tick, so only the step tells the bound's refusal from the walk-up's.
    grep -q "could not return a published phase's checkout to the integration branch" "$LOG" && lost_refused=1
    grep -q "coordination checkout sync failed" "$LOG" && lost_gate=1
    ! tmux_window_exists 2>/dev/null && lost_no_window=1
    outer_after="$(env -u GIT_CEILING_DIRECTORIES git -C "$tmp" rev-parse HEAD 2>/dev/null || true)"
    # A lost checkout is a repair, not a halt: the git ceiling still bounds every
    # git call, so the enclosing checkout must be untouched and no halt marker
    # may be written. A window may open because the tick continues.
    if [ "$outer_after" = "$outer_head" ] && [ ! -f "$HALT" ]; then
      echo "ok: a lost repository does not halt the line and leaves the enclosing checkout untouched"
    else
      echo "FAIL: a lost repository halted the line or moved the enclosing checkout (rc=$lost_rc refused=$lost_refused gate=$lost_gate no_window=$lost_no_window outer_unchanged=$([ "$outer_after" = "$outer_head" ] && echo yes || echo no))"; fail=1
    fi
  fi
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
  ARGUS="$old_argus"; NOTIFIER="$old_notifier"
  load_fixture_config "$selftest_config"
  RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
  CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
  LOG="$old_log"
}
