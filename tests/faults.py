# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The fault table for the guard harness: one entry per guard on purpose.

Each fault names the file it patches, the exact text the patch must find,
the replacement, and the self-test line that proves the fault was caught.
The harness in ``guard_faults.py`` applies them; this file is only the
data.
"""

from issue_faults import ISSUE_FAULTS

#: Every fault: what it breaks, the exact text the patch must find, the
#: replacement, and the self-test line that proves it was caught.
#:
#: ``find`` and ``replace`` are literal strings, not regular expressions, so a
#: fault can never match more than the code it was written against.
FAULTS = (
    {
        "name": "entrypoint-halt-changed",
        "breaks": "the installed command changes the shell driver's halt and notification",
        "file": "bin/argus",
        "find": "args = sys.argv[1:]",
        "replace": "args = sys.argv[1:]\nos.environ['DRIVER_STUB_RC'] = '0'\nos.environ['DRIVER_STUB_SLEEP'] = '0'",
        "check": "entrypoint",
        "verdict": "entrypoint self-test:",
        "expect": "entrypoint session changed",
    },
    {
        "name": "launchability-guard-removed",
        "breaks": "the folder cannot be read and cannot be launched from",
        "file": "src/argus/driver/lib-driver-config.sh",
        "find": 'work_preflight() {\n  PREFLIGHT_REASON=""',
        "replace": 'work_preflight() {\n  PREFLIGHT_REASON=""\n'
        "  return 0  # FAULT: guard disabled",
        "cases": "recovery_sources",
        "expect": "FAIL: a folder outside the checkout was allowed to launch",
    },
    {
        "name": "launchability-guard-below-reservation",
        "breaks": "the guard runs, but only after the phase is already claimed",
        "file": "src/argus/driver/lib-driver-drive.sh",
        "find": "  if ! work_preflight; then\n"
        '    log "$PREFLIGHT_REASON; refusing to launch"\n'
        "    return 1\n  fi\n",
        "replace": "",
        # A second patch: the guard is cut from where it belongs and pasted
        # further down, which is the shape the real defect had. One fault with
        # two edits rather than two faults, because a guard that is missing and
        # a guard that is late are the same bug seen twice.
        "second_find": "  # The public checkout is put on the phase's own branch before the launch, from",
        "second_replace": (
            "  if ! work_preflight; then\n"
            '    log "$PREFLIGHT_REASON; refusing to launch"\n'
            "    return 1\n  fi\n"
            "  # The public checkout is put on the phase's own branch before the launch, from"
        ),
        "cases": "stranding",
        # The distinguishing line. With the guard late, the tick claims first and
        # refuses second, so the board check is what fires — and that check
        # exists for exactly this ordering, which is why it is the one named here
        # rather than the launch check.
        "expect": "so the line would be stranded",
    },
    {
        "name": "config-paths-anchored-on-cwd",
        "breaks": "relative config paths name different folders when Argus runs elsewhere",
        "file": "src/argus/config/loader.py",
        "find": "        candidate = config_dir / candidate",
        "replace": "        candidate = Path.cwd() / candidate  # FAULT: anchor on cwd",
        "check": "entrypoint",
        "verdict": "entrypoint self-test:",
        "expect": "config paths resolve against the config file",
    },
    {
        "name": "fixture-config-not-passed-to-tools",
        "breaks": "the phase notifier does not receive the fixture config",
        "file": "src/argus/driver/lib-driver-notify.sh",
        "find": '  local args=("${ARGUS_CONFIG_ARGS[@]}" --phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")',
        "replace": '  local args=(--phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")  # FAULT: config not passed',
        "cases": "launch",
        # The self-test checks the notifier's real start and completion messages.
        "expect": "expected one start notification",
    },
    {
        "name": "publisher-lookup-answers-nothing-found",
        "breaks": "the selector answers without its bundled publisher",
        "file": "src/argus/roadmap/select.py",
        "find": "        from argus.gitsync.review import awaiting_review",
        "replace": "        return None  # FAULT: publisher unavailable but lookup succeeds\n        from argus.gitsync.review import awaiting_review",
        "cases": "recovery_sources",
        "expect": "--check failed on an unconfigured machine",
    },
    {
        "name": "group-declaration-missing-swallowed",
        "breaks": "a phase with no together declaration is handed a group of "
        "itself instead of being refused",
        "file": "src/argus/roadmap/group.py",
        "find": "        raise ReservationGroupMissing(\n"
        "            f\"{path} has no '### Reservation Group' section\", document=path\n"
        "        )",
        "replace": '        return [f"{_number:06d}"]  # FAULT: a missing '
        "declaration is invented",
        "check": "next_phase",
        "verdict": "next_phase self-test:",
        "expect": "a phase with no together declaration is refused by name",
    },
    {
        "name": "group-claim-not-atomic",
        "breaks": "only the first member of a group is reserved, so half a "
        "group can be claimed",
        "file": "src/argus/board/store_group.py",
        "find": "            records: list[Any] = []\n"
        "            for key in keys:\n"
        "                number = int(key)",
        "replace": "            records: list[Any] = []\n"
        "            for key in keys[:1]:  # FAULT: half a group "
        "is reserved\n"
        "                number = int(key)",
        "check": "phase_group",
        "verdict": "phase_group self-test:",
        "expect": "a group claim writes every member or none",
    },
    {
        "name": "group-already-completed-swallowed",
        "breaks": "a group whose every member is completed is claimed instead "
        "of being refused, so a finished group is reserved anew",
        "file": "src/argus/select/group.py",
        "find": "    if not remainder:",
        "replace": "    if False:  # FAULT: an all-completed group is not refused",
        "check": "phase_group",
        "verdict": "phase_group self-test:",
        "expect": "an all-completed group is refused as already completed",
    },
    {
        "name": "cooldown-refusal-swallowed",
        "breaks": "an expired claim in its cooldown is claimed instead of "
        "being refused, so two agents can run the same group",
        "file": "src/argus/board/records.py",
        "find": '        if existing.get("status") == EXPIRED_STATUS:\n'
        "            remaining = _cooldown_remaining(existing, number, now)\n"
        "            if remaining > 0:\n"
        "                raise ReservationCooldown(number, remaining, existing, kind=kind)",
        "replace": '        if existing.get("status") == EXPIRED_STATUS:\n'
        "            remaining = 0  # FAULT: the cooldown is not refused",
        "check": "phase_reservations",
        "verdict": "coordination self-test:",
        "expect": "an expired group is refused during the cooldown",
    },
    {
        "name": "release-writes-halt-marker",
        "breaks": "a release stops the run through the halt path, so it writes "
        "the halt marker instead of returning the group to the queue",
        "file": "src/argus/driver/lib-driver-run.sh",
        "find": '  if [ -f "$RELEASE" ]; then\n    rm -f "$RELEASE"\n'
        '  elif [ -n "$CUR_PHASE" ]; then',
        "replace": '  if false; then\n    rm -f "$RELEASE"\n'
        '  elif [ -n "$CUR_PHASE" ]; then',
        "check": "driver",
        "cases": "release",
        "verdict": "driver self-test:",
        "expect": "release wrote the halt marker",
    },
    {
        "name": "group-branch-ignored",
        "breaks": "a group's one branch is not asked for, so each member gets a "
        "branch of its own and the group publishes several",
        "file": "src/argus/gitsync/mode_branch.py",
        "find": "        branch=group_branch(root, group, roadmap_dir) if group "
        "else None,",
        "replace": "        branch=None,  # FAULT: the group's one branch is not used",
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the group's one branch covers every member",
    },
    {
        "name": "group-array-ignored",
        "breaks": "the group the board reports is not taken from the selection, so "
        "a member of a held group is run as a single phase on a branch "
        "of its own",
        "file": "src/argus/driver/lib-driver-select-flow.sh",
        "find": '        if [ "$work_kind" = issue ]; then group_take_selection ""; else group_take_selection "$selection"; fi\n'
        '        if [ "$work_kind" = issue ] && [ "$wait_status" = writeback_pending ]; then\n',
        "replace": "        true  # FAULT: the group array is ignored\n"
        '        if [ "$work_kind" = issue ] && [ "$wait_status" = writeback_pending ]; then\n',
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the tick recorded the group's whole array",
    },
    {
        "name": "group-renewal-removed",
        "breaks": "the tick loop never renews a running claim, so the board "
        "expires a live run's claim and another machine may take the "
        "work",
        "file": "src/argus/driver/lib-driver-drive.sh",
        "find": "  claim_renew\n",
        "replace": "  true  # FAULT: the claim is never renewed\n",
        "check": "driver",
        "cases": "group_run",
        "verdict": "driver self-test:",
        "expect": "the tick renewed the group's claim while its run was alive",
    },
    {
        "name": "selector-refusal-swallowed",
        "breaks": "a refusal with no JSON result reports a Python traceback instead of nothing",
        "file": "src/argus/driver/lib-driver-select.sh",
        "find": 'try:\n    print(json.load(sys.stdin).get(sys.argv[1], ""))\n'
        "except ValueError:\n    pass",
        "replace": 'print(json.load(sys.stdin).get(sys.argv[1], ""))',
        "cases": "launch",
        # No FAIL line is expected, and that is the honest position: nothing
        # asserts that the driver's stderr stays free of tracebacks, because the
        # traceback is cosmetic and the log line that matters is unaffected. This
        # fault is listed so it is not mistaken for a covered guard, and it is
        # checked only for the property that is asserted: the driver still works.
        "expect": None,
        "expect_absent": "driver self-test: pass",
    },
    {
        "name": "resume-gate-accepts-a-finished-run",
        "breaks": "a resume continues a run that already finished, which is the "
        "class the queue used to advance past",
        "file": "src/argus/driver/lib-driver-halt-record.sh",
        "find": "    completed)\n"
        "      printf 'the run is completed; clear the halt marker to let the queue advance' ;;\n",
        "replace": "    completed)\n      return 0 ;;\n",
        "check": "driver",
        "cases": "resume",
        "verdict": "driver self-test:",
        "expect": "a completed record did not refuse and keep the record",
    },
    {
        "name": "resume-record-consumed-before-the-launch",
        "breaks": "a refused resume consumes the record, so the halt it is standing "
        "on can no longer be retried",
        "file": "src/argus/driver/lib-driver-resume.sh",
        "find": '  local target="$requested" run_dir reason\n  RESUME_REFUSAL=""\n',
        "replace": '  local target="$requested" run_dir reason\n'
        '  RESUME_REFUSAL=""\n'
        '  rm -f "$HALT_RECORD"  # FAULT: consumed before the launch\n',
        "check": "driver",
        "cases": "resume",
        "verdict": "driver self-test:",
        "expect": "a malformed record did not refuse and keep the record",
    },
    {
        "name": "resume-not-run-on-a-cleared-marker",
        "breaks": "clearing the halt marker advances the queue instead of resuming "
        "the recorded phase",
        "file": "src/argus/driver/lib-driver-drive.sh",
        "find": '  if [ ! -f "$HALT" ] && [ -f "$HALT_RECORD" ]; then\n'
        "    RESUME_FROM_TICK=1\n"
        "    _drive_resume\n"
        "    return $?\n"
        "  fi\n",
        "replace": "  true  # FAULT: a cleared halt no longer resumes\n",
        "check": "driver",
        "cases": "resume",
        "verdict": "driver self-test:",
        "expect": "a blocked record did not resume and consume its record",
    },
    {
        "name": "resume-archives-the-run-it-continues",
        "breaks": "a resume restarts the run with --fresh, throwing away the stage "
        "state the resume exists to keep",
        "file": "src/argus/driver/lib-driver-run.sh",
        "find": '      if [ "${CLIO_RESUME:-}" = 1 ]; then\n',
        "replace": '      if [ "${CLIO_RESUME:-}" = 0 ]; then\n',
        "check": "driver",
        "cases": "resume",
        "verdict": "driver self-test:",
        "expect": "a resume restarted the run with --fresh",
    },
    {
        "name": "resume-accepts-an-unsettled-auto-pin",
        "breaks": "the resume trusts a repin that did not settle every pin on "
        "HEAD, so the run continues with pins that no longer describe the tree",
        "file": "src/argus/driver/lib-driver-ledger.sh",
        "find": '    if [ "$pin" != "$head" ]; then\n'
        '      RESUME_REFUSAL="the ledger auto-pin did not settle every pin on HEAD"\n'
        "      return 1\n"
        "    fi\n",
        "replace": "    :  # FAULT: an unsettled pin is accepted\n",
        "check": "driver",
        "cases": "resume",
        "verdict": "driver self-test:",
        "expect": "an unsettled auto-pin did not refuse",
    },
) + ISSUE_FAULTS
