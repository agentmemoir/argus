#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: the reservation group, as the driver runs it.
#
# Every case here builds its own sub-fixture, because a group case needs a
# roadmap whose lowest uncompleted phase is a member of a group, and the suite's
# own fixture is used by the cases around them. Each case saves the settings it
# changes and puts them back before it returns, the way the stranding case does:
# the suite runs every case in one process, so a case that leaves a path behind
# decides the next case's verdict.
selftest_case_group_run() {
  # A group runs one member per tick, on one branch, and the tick launches the
  # member the board named rather than the phase the selector was asked about.
  #
  # Those two are different here on purpose. The group's first member is waiting
  # on its pull request, so the selector is asked about it while the claim names
  # the second member: launching the requested file would run one member's
  # inputs as another member's phase. The fixture makes that state reachable,
  # and the driver has to answer it from the group's own array.
  #
  # The renewal is checked here too, on the next tick, because the window is
  # still open and the board measures a running group from its heartbeat.
  local group_root="$tmp/group-run-root" group_base="$tmp/group-run-base"
  local group_config="$tmp/group-run-config.json" group_branch="feature/group-cache-warm"
  local claim_out="" rc=0
  local old_repo="${REPO:-}" old_board_dir="${BOARD_DIR:-}" old_wf="${WF:-}"
  local old_argus="${ARGUS:-}" old_notifier="${NOTIFIER:-}"
  local old_run_dir="${RUN_DIR:-}" old_lock="${LOCK:-}" old_halt="${HALT:-}"
  local old_stall="${STALL:-}" old_current="${CURRENT:-}" old_pid="${PIDFILE:-}"
  local old_log="${LOG:-}" old_session="${SESSION:-}" old_claim="${CLAIM:-}"
  local old_release="${RELEASE:-}" old_group_file="${GROUPFILE:-}"
  local old_group_branch="${GROUPBRANCH:-}" old_notify_broken="${NOTIFY_BROKEN:-}"
  local old_recover="${RECOVER_OWN_CLAIM:-}"
  if ! selftest_fixture_root "$group_root" "$group_base" \
      || ! selftest_fixture_group_phases "$group_root"; then
    echo "FAIL: the group case could not build its own checkout, so no group ran"
    fail=1
    return 0
  fi
  REPO="$group_root"; BOARD_DIR=board-data
  ARGUS="$group_root/$BOARD_DIR/argus"
  NOTIFIER="$ARGUS/notify/notifications.py"
  selftest_write_config "$group_root" "$group_config" "$BOARD_DIR"
  load_fixture_config "$group_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
  RUN_DIR="$WF/$DRIVER_STATE"
  mkdir -p "$RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
  CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"; LOG="$RUN_DIR/driver.log"
  CLAIM="$RUN_DIR/claim"; RELEASE="$RUN_DIR/release"
  GROUPFILE="$RUN_DIR/group"; GROUPBRANCH="$RUN_DIR/group-branch"
  SESSION="ar-driver-group-$$"
  RECOVER_OWN_CLAIM=1
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=8
  export RECOVER_OWN_CLAIM DRIVER_STUB_RC DRIVER_STUB_SLEEP
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"

  # This machine claims the whole group through the request program, and its
  # first member is then recorded as waiting on a pull request. The receipt
  # carries no head on purpose: the merge check refuses a receipt with nothing
  # to verify without asking GitHub, so the case stays off the network and the
  # first member stays unfinished, which is what a half-run group looks like.
  claim_out="$("$PYTHON" "$ARGUS/select/group.py" "${ARGUS_CONFIG_ARGS[@]}" \
    --phase 100020 --machine-id "$PHASE_MACHINE_ID" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL: the group could not be claimed on the fixture board ($claim_out)"
    fail=1
  else
    mkdir -p "$WF/phase-100020"
    printf '{"version": 1, "phase": "100020", "state": "awaiting_review", "branch": "%s", "private_branch": "master", "integration_branch": "master"}\n' \
      "$group_branch" >"$WF/phase-100020/publication.json"
  fi

  # One tick. The board holds the group, so the selector answers the next
  # member, and this machine recovers its own claim on it.
  drive || true
  if [ "$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true)" = "$group_branch" ] \
      && git -C "$REPO" rev-parse --verify "refs/remotes/origin/$group_branch" >/dev/null 2>&1; then
    echo "ok: the group's one branch is checked out and pushed"
  else
    echo "FAIL: the group's one branch covers every member"; fail=1
  fi
  grep -q "one branch $group_branch covers" "$LOG" \
    && echo "ok: the tick named the group's one branch" \
    || { echo "FAIL: the group's one branch covers every member"; fail=1; }
  grep -q "launched phase 100030" "$LOG" \
    && echo "ok: the group's next member was launched" \
    || { echo "FAIL: the group's next member was not launched"; fail=1; }
  grep -q "next phase: 100030 (.*phase-100030-cache-read.md)" "$LOG" \
    && echo "ok: the tick named the member's own roadmap file" \
    || { echo "FAIL: the member's own roadmap file is launched"; fail=1; }
  if [ -s "$GROUPFILE" ] && grep -qx 100020 "$GROUPFILE" \
      && grep -qx 100030 "$GROUPFILE"; then
    echo "ok: the tick recorded the group's whole array"
  else
    echo "FAIL: the tick recorded the group's whole array"; fail=1
  fi

  # The next tick finds the window alive and renews the group, which is what
  # keeps a running group's claim from expiring under it.
  drive || true
  grep -q "group renew: renewed the group for phase 100030" "$LOG" \
    && echo "ok: the tick renewed the group's claim while its run was alive" \
    || { echo "FAIL: the tick renewed the group's claim while its run was alive"; fail=1; }
  wait_phase_window_gone
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"; ARGUS="$old_argus"
  NOTIFIER="$old_notifier"; RUN_DIR="$old_run_dir"; LOCK="$old_lock"
  HALT="$old_halt"; STALL="$old_stall"; CURRENT="$old_current"; PIDFILE="$old_pid"
  LOG="$old_log"; SESSION="$old_session"; CLAIM="$old_claim"; RELEASE="$old_release"
  GROUPFILE="$old_group_file"; GROUPBRANCH="$old_group_branch"
  NOTIFY_BROKEN="$old_notify_broken"
  RECOVER_OWN_CLAIM="$old_recover"; DRIVER_STUB_SLEEP=6
  export RECOVER_OWN_CLAIM DRIVER_STUB_SLEEP
  load_fixture_config "$selftest_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
}

selftest_case_group_partial() {
  # A group that died halfway. The member whose work is finished keeps its
  # record, and the members that are not go back to the queue. Release never
  # undoes finished work, and the board is the only place that can be checked:
  # the returned array says what the command meant to do, and the board says
  # what it did.
  #
  # The finished member is closed as `accepted` rather than `completed` on
  # purpose. This board requires a published receipt for `completed`, and that
  # receipt has to be present on the private remote's branch, which needs a real
  # publication. Both statuses are terminal, and terminal is the property under
  # test: a release skips them and leaves them alone.
  local group_root="$tmp/group-partial-root" group_base="$tmp/group-partial-base"
  local group_config="$tmp/group-partial-config.json"
  local claim_out="" statuses="" rc=0 rsv="" gen=""
  local old_repo="${REPO:-}" old_board_dir="${BOARD_DIR:-}" old_wf="${WF:-}"
  local old_argus="${ARGUS:-}" old_notifier="${NOTIFIER:-}"
  local old_run_dir="${RUN_DIR:-}" old_lock="${LOCK:-}" old_halt="${HALT:-}"
  local old_stall="${STALL:-}" old_current="${CURRENT:-}" old_pid="${PIDFILE:-}"
  local old_log="${LOG:-}" old_session="${SESSION:-}" old_claim="${CLAIM:-}"
  local old_release="${RELEASE:-}" old_group_file="${GROUPFILE:-}"
  local old_group_branch="${GROUPBRANCH:-}" old_notify_broken="${NOTIFY_BROKEN:-}"
  if ! selftest_fixture_root "$group_root" "$group_base" \
      || ! selftest_fixture_group_phases "$group_root"; then
    echo "FAIL: the partial-group case could not build its own checkout"
    fail=1
    return 0
  fi
  REPO="$group_root"; BOARD_DIR=board-data
  ARGUS="$group_root/$BOARD_DIR/argus"
  NOTIFIER="$ARGUS/notify/notifications.py"
  selftest_write_config "$group_root" "$group_config" "$BOARD_DIR"
  load_fixture_config "$group_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
  RUN_DIR="$WF/$DRIVER_STATE"
  mkdir -p "$RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
  CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"; LOG="$RUN_DIR/driver.log"
  CLAIM="$RUN_DIR/claim"; RELEASE="$RUN_DIR/release"
  GROUPFILE="$RUN_DIR/group"; GROUPBRANCH="$RUN_DIR/group-branch"
  SESSION="ar-driver-group-partial-$$"
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  claim_out="$("$PYTHON" "$ARGUS/select/group.py" "${ARGUS_CONFIG_ARGS[@]}" \
    --phase 100020 --machine-id "$PHASE_MACHINE_ID" 2>&1)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL: the partial-group case could not claim the group ($claim_out)"
    fail=1
  else
    read -r rsv gen < <("$PYTHON" -c 'import json, sys
try:
    records = json.load(sys.stdin)
except ValueError:
    records = []
record = records[0] if isinstance(records, list) and records else {}
print(record.get("reservation_id", ""), record.get("generation", ""))' <<<"$claim_out")
  fi
  # The member that is finished, closed through the real command so the case
  # proves the board keeps a terminal record rather than that a fixture wrote
  # one.
  claim_status_out="$("$PYTHON" "$ARGUS/board/reservations.py" set-status \
    "${ARGUS_CONFIG_ARGS[@]}" --repo-root "$REPO" --phase 100020 \
    --machine-id "$PHASE_MACHINE_ID" --reservation-id "$rsv" --generation "$gen" \
    --status accepted \
    --accept-reason "fixture: this member's work is already finished" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "ok: one member of the group is finished on the board"
  else
    echo "FAIL: the finished member could not be recorded ($claim_status_out)"; fail=1
  fi
  # The fence and the array the run would have recorded, then the release.
  printf '100030\t%s\t%s\n' "$rsv" "$gen" >"$CLAIM"
  printf '100020\n100030\n' >"$GROUPFILE"
  printf 'feature/group-cache-warm\n' >"$GROUPBRANCH"
  release || true



  # The board after the release: the finished member is still there, and the
  # member that was not finished has gone back to the queue.
  statuses="$("$PYTHON" "$ARGUS/board/reservations.py" show "${ARGUS_CONFIG_ARGS[@]}" 2>/dev/null \
    | "$PYTHON" -c 'import json, sys
try:
    phases = json.load(sys.stdin).get("phases", {})
except ValueError:
    phases = {}
print(" ".join("{}={}".format(key, record.get("status"))
               for key, record in sorted(phases.items())))')"
  case " $statuses " in
    *" 100020=accepted "*) echo "ok: the finished member stayed on the board" ;;
    *) echo "FAIL: the finished member did not stay on the board ($statuses)"; fail=1 ;;
  esac
  case " $statuses " in
    *" 100030="*) echo "FAIL: an unfinished member was left claimed ($statuses)"; fail=1 ;;
    *) echo "ok: the unfinished member went back to the queue" ;;
  esac
  [ -f "$HALT" ] \
    && { echo "FAIL: the release of a half-finished group wrote the halt marker"; fail=1; } \
    || echo "ok: the half-finished group released without a halt marker"
  grep -q 'returned the group for phase 100030 to the queue \[100020 100030' "$LOG" \
    && echo "ok: the release named the group it returned" \
    || { echo "FAIL: the release did not name the group it returned"; fail=1; }
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"; ARGUS="$old_argus"
  NOTIFIER="$old_notifier"; RUN_DIR="$old_run_dir"; LOCK="$old_lock"
  HALT="$old_halt"; STALL="$old_stall"; CURRENT="$old_current"; PIDFILE="$old_pid"
  LOG="$old_log"; SESSION="$old_session"; CLAIM="$old_claim"; RELEASE="$old_release"
  GROUPFILE="$old_group_file"; GROUPBRANCH="$old_group_branch"
  NOTIFY_BROKEN="$old_notify_broken"
  load_fixture_config "$selftest_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
}


selftest_case_group_release() {
  # A group released while one of its members is running. The whole group goes
  # back to the queue in one write, the release writes no halt marker and sends
  # no halt notification, and the run state is left clean for whoever takes the
  # group next: the window is closed, and the claim fence and the group markers
  # are cleared.
  #
  # The tick claims the group itself here, so this is also the case that covers
  # the reservation the selector makes for a whole array.
  local group_root="$tmp/group-release-root" group_base="$tmp/group-release-base"
  local group_config="$tmp/group-release-config.json"
  local wait_file="$tmp/group-release-wait" ready_file="$tmp/group-release-ready"
  local statuses="" waited=0
  local old_repo="${REPO:-}" old_board_dir="${BOARD_DIR:-}" old_wf="${WF:-}"
  local old_argus="${ARGUS:-}" old_notifier="${NOTIFIER:-}"
  local old_run_dir="${RUN_DIR:-}" old_lock="${LOCK:-}" old_halt="${HALT:-}"
  local old_stall="${STALL:-}" old_current="${CURRENT:-}" old_pid="${PIDFILE:-}"
  local old_log="${LOG:-}" old_session="${SESSION:-}" old_claim="${CLAIM:-}"
  local old_release="${RELEASE:-}" old_group_file="${GROUPFILE:-}"
  local old_group_branch="${GROUPBRANCH:-}" old_notify_broken="${NOTIFY_BROKEN:-}"
  local old_wait="${DRIVER_STUB_WAIT_FILE:-}"
  local old_ready="${DRIVER_STUB_READY_FILE:-}"
  local old_recover="${RECOVER_OWN_CLAIM:-}"
  if ! selftest_fixture_root "$group_root" "$group_base" \
      || ! selftest_fixture_group_phases "$group_root"; then
    echo "FAIL: the group-release case could not build its own checkout"
    fail=1
    return 0
  fi
  REPO="$group_root"; BOARD_DIR=board-data
  ARGUS="$group_root/$BOARD_DIR/argus"
  NOTIFIER="$ARGUS/notify/notifications.py"
  selftest_write_config "$group_root" "$group_config" "$BOARD_DIR"
  load_fixture_config "$group_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
  RUN_DIR="$WF/$DRIVER_STATE"
  mkdir -p "$RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"; STALL="$RUN_DIR/stalled"
  CURRENT="$RUN_DIR/current"; PIDFILE="$RUN_DIR/pid"; LOG="$RUN_DIR/driver.log"
  CLAIM="$RUN_DIR/claim"; RELEASE="$RUN_DIR/release"
  GROUPFILE="$RUN_DIR/group"; GROUPBRANCH="$RUN_DIR/group-branch"
  SESSION="ar-driver-group-release-$$"
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  rm -f "$wait_file" "$ready_file"
  RECOVER_OWN_CLAIM=0
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=8
  DRIVER_STUB_WAIT_FILE="$wait_file"; DRIVER_STUB_READY_FILE="$ready_file"
  export RECOVER_OWN_CLAIM DRIVER_STUB_RC DRIVER_STUB_SLEEP
  export DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  drive || true
  while [ ! -e "$ready_file" ] && [ "$waited" -lt 200 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if [ -e "$ready_file" ]; then
    echo "ok: the group-release case has a live stubbed member to release"
  else
    echo "FAIL: the group's stub never started, so the release case could not run"
    fail=1
  fi
  if [ -s "$GROUPFILE" ] && grep -qx 100030 "$GROUPFILE"; then
    echo "ok: the launch recorded the group's whole array"
  else
    echo "FAIL: the launch recorded no group array to release"; fail=1
  fi
  [ -s "$CLAIM" ] \
    && echo "ok: the tick recorded the claim fence the release needs" \
    || { echo "FAIL: the tick recorded no claim fence"; fail=1; }
  release || true
  : >"$wait_file"
  sleep 0.3
  [ -f "$HALT" ] \
    && { echo "FAIL: releasing a group wrote the halt marker"; fail=1; } \
    || echo "ok: releasing a group did not write the halt marker"
  "$PYTHON" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' \
    "$RUN_DIR/release-checkpoint.json" \
    && echo "ok: the release pushed the work in progress before the board write" \
    || { echo "FAIL: the release did not push the work in progress"; fail=1; }
  if [ ! -f "$CURRENT" ] && [ ! -f "$PIDFILE" ]; then
    echo "ok: releasing a group cleared the current-phase and pid files"
  else
    echo "FAIL: releasing a group left the current-phase or pid file behind"; fail=1
  fi
  if [ -f "$GROUPFILE" ] || [ -f "$GROUPBRANCH" ]; then
    echo "FAIL: releasing a group left its group markers behind"; fail=1
  else
    echo "ok: releasing a group cleared its group markers"
  fi
  tmux_window_exists \
    && { echo "FAIL: releasing a group left the phase window open"; fail=1; } \
    || echo "ok: releasing a group closed the phase window"
  statuses="$("$PYTHON" "$ARGUS/board/reservations.py" show "${ARGUS_CONFIG_ARGS[@]}" 2>/dev/null \
    | "$PYTHON" -c 'import json, sys
try:
    phases = json.load(sys.stdin).get("phases", {})
except ValueError:
    phases = {}
print(" ".join(sorted(phases)))')"
  case " $statuses " in
    *" 100020 "*|*" 100030 "*)
      echo "FAIL: a released group is still claimed on the board ($statuses)"; fail=1 ;;
    *) echo "ok: every member of the released group went back to the queue" ;;
  esac
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  rm -f "$CLAIM" "$GROUPFILE" "$GROUPBRANCH" "$HALT" "$CURRENT" "$PIDFILE"
  rm -f "$wait_file" "$ready_file"
  REPO="$old_repo"; BOARD_DIR="$old_board_dir"; WF="$old_wf"; ARGUS="$old_argus"
  NOTIFIER="$old_notifier"; RUN_DIR="$old_run_dir"; LOCK="$old_lock"
  HALT="$old_halt"; STALL="$old_stall"; CURRENT="$old_current"; PIDFILE="$old_pid"
  LOG="$old_log"; SESSION="$old_session"; CLAIM="$old_claim"; RELEASE="$old_release"
  GROUPFILE="$old_group_file"; GROUPBRANCH="$old_group_branch"
  NOTIFY_BROKEN="$old_notify_broken"
  DRIVER_STUB_WAIT_FILE="$old_wait"; DRIVER_STUB_READY_FILE="$old_ready"
  RECOVER_OWN_CLAIM="$old_recover"; DRIVER_STUB_SLEEP=6
  export DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE RECOVER_OWN_CLAIM DRIVER_STUB_SLEEP
  load_fixture_config "$selftest_config"
  WORK_SOURCE_FLAGS=(--source roadmap)
}
