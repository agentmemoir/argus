#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: own-claim recovery, work sources, and the readiness report.
#
selftest_case_recovery_sources() {
  # Own-claim recovery. This path had no coverage at all, which is how a
  # `set -u` abort on an unset reservation_id shipped unnoticed: a plain
  # --self-test skips coordination entirely. Three properties matter, so three
  # cases. The stub replays the selection and the takeover; the fence handling
  # and the launch itself are the real code.
  rm -f "$HALT"
  old_stub_sleep="$DRIVER_STUB_SLEEP"
  DRIVER_STUB_RC=0; DRIVER_STUB_SLEEP=2; export DRIVER_STUB_RC DRIVER_STUB_SLEEP
  RECOVER_OWN_CLAIM=0
  export RECOVER_OWN_CLAIM

  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=fence drive --self-test || true
  if grep -q "WAIT_FOR_CLAIM for phase 100010" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: recovery off leaves this host's own claim alone"
  else
    echo "FAIL: recovery=0 did not leave the claim alone"; fail=1
  fi

  RECOVER_OWN_CLAIM=1
  DRIVER_STUB_COORDINATION=fence drive --self-test || true
  sleep 0.3
  if grep -q "on rsv-recovered-stub generation 8" "$LOG"; then
    echo "ok: recovery launches and hands the child the takeover's new fence"
  else
    echo "FAIL: recovery did not carry the new fence from the takeover"; fail=1
  fi
  wait_phase_window_gone
  sleep 0.3

  # The launch path on the recovery path comes from the work source's own result,
  # not from the driver looking for the file. The driver used to glob the roadmap
  # folder here, which put roadmap knowledge back into the driver for no reason:
  # an issue source's recovery would have searched a roadmap for a file named
  # after an issue. The stub names a file the driver never opens, so if the driver
  # still searched, it would find the real phase file instead and print that.
  if grep -q "recovered phase 100010 (.*phase-100010-stub-fixture\.md)" "$LOG"; then
    echo "ok: the recovered launch path comes from the work source, not from a glob"
  else
    echo "FAIL: the recovered launch path did not come from the work source's result"; fail=1
  fi

  # A selection that names a claim but carries no launch path must stop, not fall
  # back to a guess. A guessed path opens a window that closes itself and reads as
  # a phase that ran and died.
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=nopath drive --self-test || true
  sleep 0.3
  if grep -q "returned no launch path from the work source" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: a claim with no launch path from the source refuses to launch"
  else
    echo "FAIL: a claim with no launch path was allowed to launch"; fail=1
  fi
  wait_phase_window_gone
  sleep 0.3

  launches="$(grep -c 'launched phase' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=nofence drive --self-test || true
  if grep -q "returned no new fence" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ]; then
    echo "ok: a takeover that returns no fence refuses to launch"
  else
    echo "FAIL: a fence-less takeover was allowed to launch"; fail=1
  fi

  unset DRIVER_STUB_COORDINATION
  DRIVER_STUB_SLEEP="$old_stub_sleep"; export DRIVER_STUB_SLEEP
  RECOVER_OWN_CLAIM=0
  rm -f "$HALT"

  # The two work-source cases run here, with the coordination stub unset, so the
  # real selector is reached. Under the stub the selection is a canned string
  # and the selector never runs, so a case placed earlier would pass without
  # testing anything.
  #
  # A source name the selector does not know stops the tick rather than falling
  # back to one that would work, because a fallback hides the mistake behind
  # progress.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source no-such-source)
  bogus_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "unknown work source 'no-such-source'" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$bogus_launches" ]; then
    echo "ok: an unknown work source stops the tick with no fallback"
  else
    echo "FAIL: an unknown work source did not stop the tick"; fail=1
  fi

  # The reason has to reach all three of its readers, and the one it used to
  # reach by accident is the one an operator looks at first. Under cron the
  # driver owns stderr, so a reason that stopped being written there moved the
  # message out of the cron log and into driver.log with nothing
  # saying so — an operator debugging a selection failure looked in the log they
  # have always looked in and found nothing. So the reason is captured, recorded
  # under a prefix in driver.log, and still written to stderr.
  #
  # The file and the log are covered by the cases below; this one covers the
  # stderr, which is the behaviour a document claims is unchanged and the one
  # nothing else here can see.
  #
  # The flags are set and restored rather than prefixed onto the call, because a
  # variable assignment in front of a shell function is not reliably scoped to it.
  WORK_SOURCE_FLAGS=(--source no-such-source)
  bogus_stderr="$(selector_run 2>&1 >/dev/null || true)"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
  if printf '%s' "$bogus_stderr" | grep -q "unknown work source 'no-such-source'"; then
    echo "ok: a selector refusal still reaches the driver's own stderr"
  else
    echo "FAIL: a selector refusal no longer reaches stderr, so the cron log loses it (stderr=$bogus_stderr)"; fail=1
  fi

  # A roadmap folder that is not there is a settings mistake, not a queue that
  # has run dry. The two look identical from the selector's exit code, so the
  # refusal is proved here and the folder is named.
  old_roadmap_dir="$ROADMAP_DIR"; old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  missing_root="$(mktemp -d)"
  ROADMAP_DIR="$missing_root/absent"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  missing_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "configured roadmap folder $missing_root/absent is missing or unreadable" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$missing_launches" ]; then
    echo "ok: a missing roadmap folder stops the tick and names the folder"
  else
    echo "FAIL: a missing roadmap folder did not stop the tick"; fail=1
  fi
  rm -rf "$missing_root"
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # A configured roadmap folder outside this checkout can be read but its
  # phases cannot be launched, because the launch is opened on the repository
  # root with a repository-relative phase file. Prove the refusal.
  #
  # This runs through the --self-test branch, which asks the selector without
  # --server and so never touches the shared board. It is therefore the right
  # place to check the refusal and the wrong place to check that nothing was
  # claimed: the strand fixture below is the case that does that, against a real
  # board in a throwaway repository, because claiming is the damage this guard
  # exists to prevent.
  #
  # The setting is forced either way rather than only when this machine already
  # has one: an unconfigured host is the case most deployments are in, and a
  # check that only runs on a configured one would go unexecuted exactly where
  # it is cheapest to run.
  old_roadmap_dir="$ROADMAP_DIR"; old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  outside_root="$(mktemp -d)"
  ROADMAP_DIR="$outside_root/outside"
  mkdir -p "$ROADMAP_DIR"
  # A phase file the selector will actually offer. An empty folder would return
  # NO_PHASE and the run would stop one step earlier than the refusal under
  # test, so the case would pass without reaching it.
  printf '# 100010. Outside Fixture\n- [ ] Required approval is obtained.\n' \
    >"$ROADMAP_DIR/phase-100010-outside-fixture.md"
  printf 'Plan ready\n' >"$ROADMAP_DIR/index.md"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  outside_launches="$(grep -c 'launched phase' "$LOG" || true)"
  drive --self-test || true
  if grep -q "outside $REPO, so its phase files cannot be launched" "$LOG" \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$outside_launches" ]; then
    echo "ok: a roadmap folder outside the checkout refuses to launch"
  else
    echo "FAIL: a folder outside the checkout was allowed to launch"; fail=1
  fi
  rm -rf "$outside_root"
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # Naming the default source explicitly has to work, not just be refused. Only
  # the refusals were covered before, so plumbing that dropped --source on the
  # working path would have shipped green.
  #
  # Asked of the selector directly rather than through drive, because drive goes
  # on to cut a real branch in the real checkout once a selection succeeds, and a
  # self-test has no business doing that. The end-to-end half — an explicitly
  # named source reaching a real launch — is the strand fixture's case, which
  # runs against a throwaway repository.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source roadmap)
  named_rc=0
  named_out="$(selector_run 2>/dev/null)" || named_rc=$?
  if [ "$named_rc" -eq 0 ] && [ "${named_out#*$'\t'}" != "$named_out" ]; then
    echo "ok: an explicitly named work source selects like the default"
  else
    echo "FAIL: an explicitly named work source did not reach a selection (rc=$named_rc, out=$named_out)"; fail=1
  fi
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

  # --check is the report an operator reads when a machine will not advance, so
  # a setting it cannot use has to fail the report rather than be printed inside
  # it. "work source: no-such-source" followed by a zero exit reads as a healthy
  # machine with an odd value. Both settings are set, because the report reads the
  # setting itself and the selector run is given the flag built from it.
  check_rc=0
  old_work_source="$WORK_SOURCE"
  WORK_SOURCE=no-such-source
  WORK_SOURCE_FLAGS=(--source no-such-source)
  check >/dev/null 2>&1 || check_rc=$?
  if [ "$check_rc" -ne 0 ]; then
    echo "ok: --check fails on a work source nothing recognises"
  else
    echo "FAIL: --check reported a bogus work source as healthy (exit 0)"; fail=1
  fi
  old_roadmap_dir="$ROADMAP_DIR"
  WORK_SOURCE="$old_work_source"
  WORK_SOURCE_FLAGS=()
  ROADMAP_DIR="$tmp/check-absent-roadmap"
  WORK_SOURCE_FLAGS=(--roadmap "$ROADMAP_DIR")
  check_rc=0
  check >/dev/null 2>&1 || check_rc=$?
  if [ "$check_rc" -ne 0 ]; then
    echo "ok: --check fails on a roadmap folder that is missing"
  else
    echo "FAIL: --check reported a missing roadmap folder as healthy (exit 0)"; fail=1
  fi
  ROADMAP_DIR="$old_roadmap_dir"
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")
  check_rc=0
  # The report is kept rather than discarded. `--check` is the report an operator
  # reads when a machine will not advance, so a case that fails it has to be able
  # to say which line of the report objected; a bare exit code sends the next
  # person back to the source to find out.
  check >"$RUN_DIR/check-unconfigured.out" 2>&1 || check_rc=$?
  if [ "$check_rc" -eq 0 ]; then
    echo "ok: --check passes on an unconfigured machine"
  else
    echo "FAIL: --check failed on an unconfigured machine (exit $check_rc)"
    # The lines that object, not the whole report: the report is a JSON dump of
    # two self-tests and the objecting line is one word in it. Each pipeline is
    # wrapped in a group with `|| true` because under `pipefail` a grep that
    # finds nothing fails the pipeline, and a bare failing pipeline stops the
    # run. A diagnostic that stops the run it is diagnosing is worse than none.
    { grep -iE 'MISSING|WARNING|REFUS|unreadable|traceback|error' \
        "$RUN_DIR/check-unconfigured.out" 2>/dev/null \
      | sed 's/^/      /' | head -12; } || true
    { grep -B4 '"ok": false' "$RUN_DIR/check-unconfigured.out" 2>/dev/null \
      | sed 's/^/      /' | head -20; } || true
    fail=1
  fi

}
