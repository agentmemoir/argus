#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The driver self-test: fixture directory, setup, teardown, and case order.
#
# A temporary directory whose shell scripts this host will actually run.
#
# The self-test copies the driver's own code into a fixture root and hands a
# path inside it to tmux, which execs the file directly. That only works when the
# filesystem permits execution: some hosts mount the default temporary directory
# `noexec`, and there tmux cannot start the fixture's idle script at all. The
# refusal names the script, so it reads as a driver fault rather than a property
# of the mount.
#
# The candidates are tried in order and the first one that runs an executable
# file is used, which is the same rule the repository's Python suites follow.
# A host with none of them gets a stated skip naming the mount, because a skip
# that says why cannot be mistaken for a passing check.
# Asks Argus's bundled helper for the directory rather than restating its
# list of candidates here. One rule, one place: a second copy in this repository is
# a second thing to update when the first moves, and this one lives in another
# checkout entirely, so it is the copy nobody would remember.
#
# The directory is created by that helper but not removed by it. A finaliser bound
# to the helper's process would delete the directory the moment this function
# returned, so the self-test owns the removal instead; see `driver_self_test`.
selftest_tmpdir() {
  local helper="$ARGUS/exec_tempdir.py" leftover
  # Fixtures abandoned by an earlier run are removed here, so an interrupted run is
  # cleaned up by the next one rather than waiting for a reboot. Keyed on this file's
  # own prefix and limited to directories, so nothing outside the fixtures can be
  # caught by it.
  #
  # Only ones older than an hour, because two self-tests on one machine would
  # otherwise delete each other's fixture: this sweep cannot tell an abandoned
  # directory from one another run is using right now, and a fresh one is
  # indistinguishable from that. The age is what tells them apart.
  for leftover in /tmp/argus-selftest-* /dev/shm/argus-selftest-* \
                  "$(dirname "$ARGUS")"/target/argus-selftest-*; do
    [ -d "$leftover" ] || continue
    # `find` prints the directory only when it is older than the age asked for, so
    # an empty answer means too young to be abandoned.
    [ -n "$(find "$leftover" -maxdepth 0 -mmin +60 2>/dev/null)" ] || continue
    rm -rf "$leftover" 2>/dev/null
  done
  # On stderr, not stdout: the caller captures this function's stdout to take the
  # directory path, so anything printed there would be swallowed into the path.
  # At this point the run directory may not exist yet, and a skip whose reason
  # never reaches the reader is indistinguishable from a skip because the host has
  # nowhere to run a script.
  if [ ! -f "$helper" ]; then
    printf 'self-test: the bundled temporary directory helper is missing: %s\n' "$helper" >&2
    return 1
  fi
  "$PYTHON" - "$helper" <<'PY' 2>/dev/null
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[1])))
try:
    from exec_tempdir import make_exec_dir
except ImportError:
    raise SystemExit(1)
directory = make_exec_dir()
if not directory:
    raise SystemExit(1)
# Left in place on purpose: the caller outlives this process and removes it.
print(directory)
PY
}

# Run one named case unless SELFTEST_CASES names a subset. Unset or empty runs
# every case, so the default suite is unchanged. The fault harness sets it so a
# fault runs only the case that catches it instead of all twelve. A name typo
# skips a case, which shows up as the missing FAIL line, not as a pass.
selftest_run_case() {
  local name="$1"
  if [ -n "${SELFTEST_CASES:-}" ]; then
    case " $SELFTEST_CASES " in
      *" $name "*) ;;
      *)
        echo "case skipped: $name (SELFTEST_CASES)"
        return 0
        ;;
    esac
  fi
  "selftest_case_$name"
}

driver_self_test() {
  local fail=0 tmp selftest_wf
  # Refuse a name that matches no case: a case skipped by a typo would
  # otherwise read as a pass.
  if [ -n "${SELFTEST_CASES:-}" ]; then
    local wanted
    for wanted in $SELFTEST_CASES; do
      if ! declare -F "selftest_case_$wanted" >/dev/null; then
        echo "FAIL: SELFTEST_CASES names an unknown case: $wanted" >&2
        return 1
      fi
    done
  fi
  if ! tmp="$(selftest_tmpdir)"; then
    echo "skip: no fixture directory could be built, so no check ran"
    echo "      (a directory has to permit running a shell script; a home directory is not"
    echo "       a candidate, because it is not scratch space. The reason above, if any,"
    echo "       names which of the two it was.)"
    # 77 is the conventional exit for "skipped, nothing ran", and it is not 0.
    # A caller that reads an exit status has to be able to tell a skip from a pass:
    # a run that executed no checks has verified nothing, and reporting it as a
    # pass is the same mistake in a quieter form.
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  # The fixture tree and every tmux session this run creates are removed on the
  # way out, and they are removed from a trap because a run that stops part way
  # never reaches the teardown at the end of the body. A session left behind keeps
  # an idle-phase monitor running that goes on forking after the run printed its
  # last line, and one such session per run is what a list of sessions turns into
  # for whoever is trying to find a stuck phase.
  #
  # The session names are this shell's own pid joined to the prefix each case
  # uses, so the trap can remove a session a case renamed into place and then died
  # before putting the previous name back.
  #
  # These three are named without `local` on purpose. The trap runs after
  # driver_self_test has returned, so a name scoped to that function would be gone
  # by the time the trap needed it, and an unset name under `set -u` would stop
  # the trap on its first line and leave the session behind again.
  SELFTEST_TMP="$tmp"
  export TMPDIR="$tmp"
  TMUX_TMPDIR="$tmp/tmux"
  mkdir -p "$TMUX_TMPDIR"
  export TMUX_TMPDIR
  SELFTEST_SESSION="ar-driver-selftest-$$"
  SELFTEST_REACHED_END=0
  selftest_teardown() {
    local rc=$? violations=""
    # A run that stopped before its last check reports failure even when the status
    # says otherwise.
    #
    # The status cannot be the signal, and the reason is worth writing down
    # because it is neither portable nor what it looks like. Measured on this
    # machine's bash 3.2.57 and on bash 5.3.20, with `set -euo pipefail` and a body
    # that stops early:
    #
    #   an unbound variable, no trap      bash 3.2 -> 1    bash 5.3 -> 1
    #   an unbound variable, EXIT trap    bash 3.2 -> 0    bash 5.3 -> 1
    #   an unbound variable, a trap that
    #     re-exits with the saved status  bash 3.2 -> 0    bash 5.3 -> 1
    #   a command that returned false     both -> 1
    #   an empty array expansion          bash 3.2 -> an error   bash 5.3 -> not one
    #
    # So on bash 3.2 an installed EXIT trap swallows the abort and reports 0, and
    # preserving the status inside the trap does not help. On bash 4.4 and later
    # the empty-array expansion that used to trigger this is not an error at all,
    # so that trigger is version-specific while the hole underneath it is not. A
    # flag the run sets for itself is the only mechanism that works on all of them.
    #
    # An `if` and not an `&&` chain, because a chain that ends false is itself a
    # failing command and `set -e` would stop the teardown on its first line,
    # which is the session leak this exists to stop. A status the run chose on
    # purpose, such as the skip code, is left alone.
    if [ "$rc" -eq 0 ] && [ "$SELFTEST_REACHED_END" -eq 0 ]; then
      rc=1
      echo "FAIL: the self-test stopped before its last check" >&2
    fi
    # The tripwire, here rather than at the end of the body. A check at the end of
    # the body is not run at all by the run that needs it, and the run that needs
    # it is the one that stops half way. This run is pointed at a throwaway
    # checkout, so nothing below should ever fire; it stays because a layout that
    # stops being honest has to say so rather than be found by whoever's tree
    # moved. See argus/driver/selftest-support.sh.
    violations="$(selftest_real_state_violations 2>&1)" || rc=1
    if [ -n "$violations" ]; then
      printf '%s\n' "$violations" | while IFS= read -r line; do
        [ -n "$line" ] || continue
        printf 'FAIL: %s\n' "$line" >&2
      done
    fi
    # One machine-readable line for a caller that wants the verdict rather than
    # the prose. What it adds is that a stopped run, a moved checkout and a
    # failed assertion are all the same kind of event here, instead of one being
    # a line of text and the others being an exit code. The counts of passed and
    # failed checks stay in the ok and FAIL lines, which is where a reader looks
    # and where the fault harness already reads them.
    printf 'SELFTEST_RESULT {"exit":%s,"reached_end":%s,"violations":%s}\n' \
      "$rc" \
      "$([ "$SELFTEST_REACHED_END" -eq 1 ] && printf true || printf false)" \
      "$(printf '%s\n' "$violations" | grep -c . || true)"
    tmux kill-session -t "$SELFTEST_SESSION" 2>/dev/null || true
    tmux kill-session -t "ar-driver-coord-failure-$$" 2>/dev/null || true
    tmux kill-session -t "ar-hook-selftest-$$" 2>/dev/null || true
    tmux kill-session -t "ar-driver-strand-$$" 2>/dev/null || true
    # The dead-branch case creates this one and kills it only on its own happy
    # path, so a case that aborts before that leaves it running. It belongs in
    # the trap for the same reason the others do.
    tmux kill-session -t "ar-driver-dead-branch-$$" 2>/dev/null || true
    # The group cases name their sessions after the case and this process, so a
    # case that stops before its own cleanup leaves one running.
    tmux kill-session -t "ar-driver-group-$$" 2>/dev/null || true
    tmux kill-session -t "ar-driver-group-partial-$$" 2>/dev/null || true
    tmux kill-session -t "ar-driver-group-release-$$" 2>/dev/null || true
    # A failing run keeps its fixture when asked, because the reason a case
    # failed is usually in a file the fixture owned and the teardown deletes.
    if [ "${SELFTEST_KEEP_TMP:-0}" = "1" ] && [ "$rc" -ne 0 ]; then
      printf 'selftest fixture kept: %s\n' "$SELFTEST_TMP" >&2
    else
      rm -rf "$SELFTEST_TMP"
    fi
    exit "$rc"
  }
  trap selftest_teardown EXIT HUP INT TERM
  SESSION="$SELFTEST_SESSION"
  SESSION_NAME="$SESSION"
  # Read the machine's real state before this run points itself anywhere, so the
  # exit trap has something to compare against. The comparison happens in the
  # trap, not at the end of this body; see selftest_teardown.
  selftest_real_state_capture
  # A throwaway checkout for the whole suite, built before the first case runs.
  #
  # This suite used to run its cases against the machine's real checkouts and
  # guard against reaching them two ways: a stub, and a check at the end of this
  # body. Both guards failed, and each failed in the way that hid the other. The
  # stub skipped the very step that moves a checkout, so the step was never
  # tested; the check sat at the end, so the run that most needed it, the one
  # that stopped half way, never reached it. Two runs of this suite moved a
  # developer's public checkout, and one pushed a branch to the production
  # remote, before either was recorded.
  #
  # So the suite no longer has a real checkout to reach. Every case from here on
  # runs against a fixture with its own public checkout, private checkout, board
  # and roadmap, and the driver is pointed at it before anything is invoked. The
  # tripwire stays, because a layout that stops being honest still has to be
  # reported, but it is now a net under a floor rather than the floor.
  BOARD_DIR=board-data
  selftest_root="$tmp/selftest-root"
  if ! selftest_fixture_root "$selftest_root" "$tmp/selftest-base"; then
    echo "FAIL: the self-test's throwaway checkout could not be built, so no case ran" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  REPO="$selftest_root"
  # The stall check measures a phase directory, and the phase number it reads comes
  # from `$CURRENT`. It has to be a directory this test created: pointing it at a
  # phase the checkout happens to have, or at one it does not, decides the answer by
  # what is on disk rather than by the code under test. An absent directory measures
  # zero and the check returns early, which is why this reported no stall on a
  # healthy host.
  #
  # Saved and restored like the fixture section further down, so this one stops
  # depending on being the last thing that touched it.
  selftest_wf="${WF:-}"
  # The fixture's own configuration, written here and read through the same
  # module a tick uses. Every value the cases use comes from that file, so the
  # suite carries no layout knowledge and no ambient setting of its own: an empty
  # environment is enough to run it.
  selftest_config="$tmp/selftest-config.jsonc"
  if ! selftest_write_config "$selftest_root" "$selftest_config" "$BOARD_DIR"; then
    echo "FAIL: the self-test's fixture configuration could not be written" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  if ! load_fixture_config "$selftest_config"; then
    echo "FAIL: the self-test's fixture configuration could not be read" >&2
    echo "driver self-test: skip (exit 77)"
    return 77
  fi
  WORK_SOURCE_FLAGS=(--source roadmap)
  # The driver's own state lives inside the configured run folder, so a child
  # tick, which derives the same path from the same file, writes its log, halt
  # marker and pid where this suite reads them.
  DRIVER_RUN_DIR="$WF/$DRIVER_STATE"
  RUN_DIR="$DRIVER_RUN_DIR"
  ARGUS="$REPO/$BOARD_DIR/argus"
  export PATH="$ARGUS/bin:$PATH"
  NOTIFIER="$ARGUS/notify/notifications.py"
  mkdir -p "$WF"
  # Pass the fixture config to each child tool. The probe below asks the
  # notifier to resolve its run folder using that same config.
  set_config_args
  load_board_override() {
    local board_path="$1" config_path="$tmp/board-override-config.json"
    selftest_write_config "$REPO" "$config_path" "$BOARD_DIR" "$board_path"
    load_fixture_config "$config_path"
  }

  # And that the telling worked. A fixture which points the driver at a
  # throwaway checkout must leave every tool reading the throwaway, and the only
  # way to know it does is to ask a tool where it is looking. The notifier is the
  # one tool here that answers that question on the command line, so it is asked
  # directly rather than inferred from a notification that happened to look right.
  if ! layout_probe="$("$PYTHON" - "$ARGUS" "${ARGUS_CONFIG_ARGS[@]}" <<'PROBE' 2>&1
import sys

sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[2:])
from argus.notify.notifications import default_run_json
print(default_run_json("phase-100010-probe.md").parent.parent)
PROBE
  )"; then
    layout_probe="error: $layout_probe"
  fi
  case "$layout_probe" in
    "$WF") echo "ok: the notifier reads the fixture's run state, not the machine's" ;;
    *)
      echo "FAIL: the notifier reads run state from $layout_probe while the fixture reads $WF"
      fail=1
      ;;
  esac
  # Keep the failed probe above as evidence, then restore the fixture settings
  # so later checks still run when the publication guard is deliberately broken.
  set_config_args
  DISABLE_NOTIFY=1
  DISABLE_DISCORD=1
  DRIVER_STUB_RC=0
  # Long enough that the phase window is still open for the checks below. The
  # phase now lives in a window that closes on exit, so a 1s stub could finish
  # before the first assertion ran.
  DRIVER_STUB_SLEEP=6
  export SESSION_NAME DRIVER_RUN_DIR DISABLE_NOTIFY DISABLE_DISCORD DRIVER_STUB_RC DRIVER_STUB_SLEEP
  RUN_DIR="$DRIVER_RUN_DIR"
  LOCK="$RUN_DIR/driver.lock"; HALT="$RUN_DIR/halted"
  STALL="$RUN_DIR/stalled"; CURRENT="$RUN_DIR/current"
  PIDFILE="$RUN_DIR/pid"; CLAIM="$RUN_DIR/claim"; RELEASE="$RUN_DIR/release"
  GROUPFILE="$RUN_DIR/group"; GROUPBRANCH="$RUN_DIR/group-branch"
  NOTIFY_BROKEN="$RUN_DIR/notify-broken"; LOG="$RUN_DIR/driver.log"
  mkdir -p "$RUN_DIR"
  echo "self-test session: $SESSION  state dir: $RUN_DIR"

  title_of() { tmux show-option -t "$SESSION" -v set-titles-string 2>/dev/null || true; }

  selftest_run_case launch
  selftest_run_case locks_stall
  selftest_run_case flow
  selftest_run_case release
  selftest_run_case unavailable
  selftest_run_case reasons
  selftest_run_case recovery_sources
  selftest_run_case merge_orphan
  selftest_run_case stranding
  # The group cases last: they build their own sub-fixtures, so they are the
  # cases that would notice a suite-level setting another case left behind.
  selftest_run_case group_run
  selftest_run_case group_partial
  selftest_run_case group_release


  tmux kill-session -t "$SESSION" 2>/dev/null || true
  WF="$selftest_wf"
  # Reached the last check, so a stop from here on is a stop the run chose.
  SELFTEST_REACHED_END=1
  # The trap is deliberately left armed. It is what removes the fixture tree, and
  # it is what runs the tripwire, and it runs on this path as much as on an abort.
  # Nothing it does can fail here: `rm -rf` on an absent path succeeds, and every
  # `tmux kill-session` is guarded. Removing the fixture and disarming the trap
  # from here instead would mean the tripwire never runs on a successful run,
  # which is the only kind of run most of them are.
  echo "driver self-test: $([ "$fail" = 0 ] && echo pass || echo FAIL)"
  return "$fail"
}
