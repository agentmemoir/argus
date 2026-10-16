#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: the stranding fixture against the real board of a throwaway repo.
#
selftest_case_stranding() {
  # Stranding case. A phase leaves the index dirty for its whole life, so a
  # remote that moves during the phase must not stop the next tick from
  # reaching the launch. This is the case that stranded phase 100800: the
  # refusal happened before the claim-recovery path, so neither the approval
  # nor the halt marker could unblock the line.
  #
  # The fixture is real: two bare remotes, two checkouts, a staged and an
  # unstaged edit in each, and a clean commit pushed to each remote that
  # touches a different path. Only the agent harness is stubbed.
  strand_root="$tmp/strand-root"
  strand_run="$tmp/strand-run"
  strand_ok=1
strand_diagnose() {
  echo "--- strand driver.log ---"
  sed 's/^/    /' "$LOG" 2>/dev/null || echo "    (no log)"
  if [ -f "$RUN_DIR/coordination-sync.json" ]; then
    echo "--- strand coordination-sync.json ---"
    "$PYTHON" -c 'import json,sys
for r in json.load(open(sys.argv[1])).get("repos", []):
    print("   ", r.get("repo"), r.get("relation"), r.get("ok"),
          r.get("blockers"), r.get("overlapping"), r.get("dirty"), r.get("staged"))
' "$RUN_DIR/coordination-sync.json" 2>/dev/null || \
      sed 's/^/    /' "$RUN_DIR/coordination-sync.json"
  fi
}
STRAND_BOARD=""
strand_board_read() {
  # Read the board file and branch named by this fixture's config.
  local state_rel="${BOARD_FILE#"${BOARD_REPO%/}"/}"
  STRAND_BOARD="$(git -C "$BOARD_REPO" \
    show "$BOARD_REMOTE/$PHASE_COORDINATION_BRANCH:$state_rel" \
    2>/dev/null || true)"
}
# Run one tick with the settings the caller has already put in place, and report
# whether it refused without opening a window or writing to the board.
#
#   $1  text the driver's log must carry, naming the refusal
#
# This is the harness a post-reservation refusal has to be checked with, and the
# strand fixture is the only place it can be used: the cases in the main
# self-test run through the --self-test branch, which asks the selector without
# --server and therefore never claims anything at all. A check on that branch
# cannot see the damage, because the damage is a claim.
#
# Both of the cheap signals are wrong on their own. The log proves the driver
# said no, and the tick's exit code is not consulted at all because a refusal is
# supposed to fail the tick. What has to be checked is the board, because a
# claim taken by a tick that then refuses is the one failure the line cannot
# clear by itself: every later tick reads that claim back as WAIT_FOR_CLAIM,
# logs it, and exits 0, so the line stops silently and only an operator takeover
# restarts it. The whole file is compared rather than one phase, because a claim
# under any name at all is a claim.
#
# 0 refused as asked and the board came back byte-for-byte, 1 it launched or
# logged something else, 2 it wrote to the board, 3 the board could not be read,
# in which case the case proves nothing and the caller must report a failure
# rather than pass it.
strand_expect_refused() {
  local expect="$1" before after launches
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  strand_board_read
  before="$STRAND_BOARD"
  # The fixture's own claim on 100010 is the evidence this board is really being
  # read. An unreadable board answers "unchanged" for exactly the same reason an
  # unclaimed phase would, so without this the case passes on a broken read.
  printf '%s' "$before" | grep -q '"100010"' || return 3
  drive >/dev/null 2>&1 || true
  grep -q "$expect" "$LOG" || return 1
  [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ] || return 1
  strand_board_read
  after="$STRAND_BOARD"
  [ -n "$after" ] || return 3
  [ "$after" = "$before" ] || return 2
  return 0
}
  # The stranding case needs its own root rather than the suite's, because it
  # needs pre-existing state the suite's root must not have: a staged and an
  # unstaged edit in each checkout, and a clean commit already pushed by another
  # developer. Both fixtures come from the same two helpers, so a change to the
  # builder reaches both.
  if selftest_fixture_root "$strand_root" "$tmp/strand" \
      && selftest_fixture_stage_work "$strand_root" "$tmp/strand"; then
    old_repo="$REPO"; old_board_dir="$BOARD_DIR"; old_wf="$WF"
    old_run_dir="$RUN_DIR"; old_lock="$LOCK"; old_halt="$HALT"; old_stall="$STALL"
    old_current="$CURRENT"; old_pid="$PIDFILE"; old_notify_broken="$NOTIFY_BROKEN"
    old_log="$LOG"; old_session="$SESSION"; old_argus="$ARGUS"
    old_notifier="$NOTIFIER"; old_pipeline="$PIPELINE"
    old_phases_dir="$PHASES_DIRECTORY"; old_work_source="$WORK_SOURCE"
    old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
    REPO="$strand_root"; BOARD_DIR=board-data
    ARGUS="$strand_root/$BOARD_DIR/argus"
    NOTIFIER="$ARGUS/notify/notifications.py"
    # A configuration file for this sub-fixture, so every tick it launches reads
    # the strand checkout rather than the suite's.
    ARGUS_CONFIG_FILE="$tmp/strand-config.jsonc"
    selftest_write_config "$strand_root" "$ARGUS_CONFIG_FILE" "$BOARD_DIR"
    load_fixture_config "$ARGUS_CONFIG_FILE"
    WORK_SOURCE_FLAGS=(--source phases)
    strand_run="$WF/$DRIVER_STATE"
    mkdir -p "$strand_run"
    RUN_DIR="$strand_run"
    LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
    CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"
    NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
    SESSION="ar-driver-strand-$$"
    # Only the agent harness is stubbed. Coordination and the sync are the real
    # code, because a stubbed board would skip the sync, which is the code
    # under test here.
    DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=1
    export DRIVER_STUB_RC DRIVER_STUB_SLEEP

    # First, the claim must still be respected when the operator has not asked
    # for a takeover. The sync has to pass to reach the claim at all, so a
    # refusal here would mask the thing being tested.
    drive >/dev/null 2>&1 || true
    if grep -q "WAIT_FOR_CLAIM for phase 100010" "$LOG"; then
      echo "ok: recovery off still waits for this host's own claim"
    else
      echo "FAIL: recovery=0 did not wait for the claim"; strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # Every way this machine can be configured wrongly must be refused above the
    # reservation, and the claim is the damage: a tick that claims and then
    # refuses leaves a live claim on the shared board, every later tick reads it
    # back as WAIT_FOR_CLAIM, logs it, and exits 0, so the line stops silently and
    # only an operator takeover restarts it. That is why the refusal sits above
    # the selector here and not beside the launch.
    #
    # These are the cases that can see it. The ones in the main self-test ask the
    # selector without --server and therefore never claim, so they cannot observe
    # the thing that goes wrong. These drive the real reserving path against a
    # board of their own and read that board afterwards, which is the only way to
    # find out what a refused tick left behind.
    #
    # All three refusals are covered rather than only the outside-folder one,
    # because they are refused in different places: the folder is checked in the
    # driver's shell before the selector runs, and the source name is checked
    # inside the selector. One of the three can move below the reservation
    # without the other two noticing, and a move is exactly what a later edit
    # makes when someone tidies the ordering.
    #
    # The phase each folder offers is one the fixture board knows nothing about,
    # so a claim appears only if this tick takes one. Nothing else in the fixture
    # refers to them.
    strand_outside="$tmp/strand-outside-phases"
    strand_unclaimed=100099
    mkdir -p "$strand_outside"
    printf '# %s. Outside Strand Fixture\n- [ ] Required approval is obtained.\n' \
      "$strand_unclaimed" >"$strand_outside/phase-$strand_unclaimed-outside-strand.md"
    printf 'Plan ready\n' >"$strand_outside/index.md"
    PHASES_DIRECTORY="$strand_outside"
    WORK_SOURCE_FLAGS=(--phases "$strand_outside")
    if strand_expect_refused \
        "outside $REPO, so its phase files cannot be launched"; then
      echo "ok: a folder outside the checkout refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: a folder outside the checkout was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: a folder outside the checkout was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # A folder that is not there at all. Same claim to strand, same place to
    # refuse, and it is the one a typo in a settings file produces.
    PHASES_DIRECTORY="$tmp/strand-absent-phases"
    WORK_SOURCE_FLAGS=(--phases "$PHASES_DIRECTORY")
    if strand_expect_refused \
        "configured phases folder $tmp/strand-absent-phases is missing or unreadable"; then
      echo "ok: a missing phases folder refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: a missing phases folder was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: a missing phases folder was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # A source name nothing recognises. This one is refused by the selector rather
    # than by the driver's shell, so it is the case that would pass if only the
    # shell's own check were trusted: the shell does not check the source at all,
    # and the selector's refusal has to arrive before anything is claimed.
    PHASES_DIRECTORY=""
    WORK_SOURCE_FLAGS=(--source no-such-source)
    if strand_expect_refused "unknown work source 'no-such-source'"; then
      echo "ok: an unknown work source refuses, and the board is untouched"
    else
      case $? in
        2) echo "FAIL: an unknown work source was allowed to claim, so the line would be stranded";;
        3) echo "FAIL: the fixture board could not be read, so the no-claim check proves nothing";;
        *) echo "FAIL: an unknown work source was allowed to launch";;
      esac
      strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone

    # Back to the fixture's own folder: every case below selects from it.
    PHASES_DIRECTORY=""; WORK_SOURCE_FLAGS=()

    # Then the takeover: the tick must reach the launch with the phase's work
    # still in the index and both remotes moved.
    before_index="$(git -C "$strand_root/$BOARD_DIR" diff --cached)"
    before_fix="$(cat "$strand_root/$BOARD_DIR/phase.txt")"
    RECOVER_OWN_CLAIM=1; export RECOVER_OWN_CLAIM
    drive >/dev/null 2>&1 || true
    if grep -q "launched phase" "$LOG" \
        && ! grep -q "coordination checkout sync failed" "$LOG"; then
      echo "ok: a dirty index and a moved remote still reach the launch"
    else
      echo "FAIL: a moved remote with a dirty index stopped the launch"; strand_ok=0; fail=1
      strand_diagnose
    fi
    if [ "$(git -C "$strand_root/$BOARD_DIR" diff --cached)" = "$before_index" ] \
        && [ "$(cat "$strand_root/$BOARD_DIR/phase.txt")" = "$before_fix" ]; then
      echo "ok: the review baseline survives the sync untouched"
    else
      echo "FAIL: the sync changed the phase's staged or unstaged work"; strand_ok=0; fail=1
    fi
    wait_phase_window_gone

    # An explicitly named work source must reach a real launch, not only be
    # refused. Naming the source rather than letting it default is the setting an
    # operator sets by hand, and it has to drive the whole path: selection,
    # reservation, branch cut and window. Safe here and nowhere else, because
    # this repository and this board are the fixture's own.
    WORK_SOURCE_FLAGS=(--source phases)
    # Counts, not presence. The log is cumulative and an earlier case in this
    # same fixture refused an unknown source on purpose, so a presence test would
    # read that deliberate refusal as this tick failing.
    named_launches="$(grep -c 'launched phase' "$LOG" || true)"
    named_refusals="$(grep -c 'unknown work source' "$LOG" || true)"
    drive >/dev/null 2>&1 || true
    if [ "$(grep -c 'launched phase' "$LOG" || true)" -gt "$named_launches" ] \
        && [ "$(grep -c 'unknown work source' "$LOG" || true)" = "$named_refusals" ]; then
      echo "ok: an explicitly named work source drives a full launch"
    else
      echo "FAIL: an explicitly named work source did not reach a launch"; strand_ok=0; fail=1
      strand_diagnose
    fi
    wait_phase_window_gone
    WORK_SOURCE_FLAGS=()

    # A genuine overlap must still stop the line, and must say which path.
    # The phase is on its own branch now, so the incoming commit has to arrive
    # on that branch: remote movement on the integration branch no longer
    # reaches the phase until the branch is merged, which is the point of it.
    strand_branch="feature/strand-fixture"
    if git -C "$strand_root/$BOARD_DIR" rev-parse --verify "$strand_branch" >/dev/null 2>&1; then
      git clone -q -b "$strand_branch" "$tmp/strand/priv.git" \
        "$tmp/strand/other-overlap" 2>/dev/null || true
      if [ -d "$tmp/strand/other-overlap" ]; then
        printf 'three\n' >>"$tmp/strand/other-overlap/b.txt"
        git -C "$tmp/strand/other-overlap" add -A
        git -C "$tmp/strand/other-overlap" commit -q -m "other: touch b.txt on the phase branch"
        git -C "$tmp/strand/other-overlap" push -q origin "$strand_branch"
        rm -rf "$tmp/strand/other-overlap"
        # Make the phase's edit overlap the incoming commit.
        git -C "$strand_root/$BOARD_DIR" add phase.txt
        printf 'phase baseline\nremediator fix\n' >"$strand_root/$BOARD_DIR/b.txt"
        git -C "$strand_root/$BOARD_DIR" add b.txt
        rm -f "$HALT"
        echo "--- overlap precondition ---"
        echo "    on branch: $(git -C "$strand_root/$BOARD_DIR" rev-parse --abbrev-ref HEAD)"
        echo "    incoming: $(git -C "$strand_root/$BOARD_DIR" fetch -q origin && git -C "$strand_root/$BOARD_DIR" diff --name-only --no-renames HEAD "origin/$strand_branch" | tr '\n' ' ')"
        echo "    dirty:    $(git -C "$strand_root/$BOARD_DIR" status --porcelain=v1 --untracked-files=all | tr '\n' '|')"
        log_count_before="$(grep -c 'launched phase' "$LOG" || true)"
        drive >/dev/null 2>&1 || true
        if grep -q "coordination checkout sync failed" "$LOG" \
            && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$log_count_before" ]; then
          echo "ok: a genuine overlap still refuses the launch"
        else
          echo "FAIL: an overlapping path did not refuse the launch"; strand_ok=0; fail=1
          strand_diagnose
        fi
        if grep -q '"overlapping"' "$RUN_DIR/coordination-sync.json" 2>/dev/null; then
          echo "ok: the overlap refusal names the overlapping path"
        else
          echo "FAIL: the overlap refusal did not name a path"; strand_ok=0; fail=1
          strand_diagnose
        fi
      fi
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"
    RUN_DIR="$old_run_dir"; LOCK="$old_lock"; HALT="$old_halt"; STALL="$old_stall"
    CURRENT="$old_current"; PIDFILE="$old_pid"; NOTIFY_BROKEN="$old_notify_broken"
    LOG="$old_log"; SESSION="$old_session"; ARGUS="$old_argus"
    NOTIFIER="$old_notifier"; PIPELINE="$old_pipeline"
    PHASES_DIRECTORY="$old_phases_dir"; WORK_SOURCE="$old_work_source"
    WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
    load_fixture_config "$selftest_config"
  else
    # The builder writes its own reason to stderr, and stderr was not captured
    # here so that it reaches the reader; this line says which stage failed.
    echo "FAIL: could not build the stranding fixture, so no stranding case ran"
    strand_ok=0; fail=1
  fi
}
