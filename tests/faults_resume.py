# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Faults aimed at the halt-resume and abandon guards.

Split from ``faults.py`` so that table stays within the repository's Python line
limit; the harness concatenates them, so the split changes nothing about how the
faults run. Each fault names the self-test case and FAIL line that proves it was
caught.
"""

#: Faults that break one resume or abandon guard at a time.
RESUME_FAULTS = (
    {
        "name": "resume-gate-accepts-a-damaged-run",
        "breaks": "a damaged run.json is resumed as an interrupted run",
        "file": "src/argus/driver/lib-driver-halt-record.sh",
        "find": '  if [ -f "$run_dir/run.json" ]; then\n',
        "replace": "  if false; then  # FAULT: a present run.json is not refused\n",
        "cases": "resume",  # selftest_resume_gate_cases
        "verdict": "driver self-test:",
        "expect": "a damaged run.json stopped the resume instead of repairing it",
    },
    {
        "name": "resume-consumes-an-unowned-record",
        "breaks": "a resume deletes a halt record that names another phase",
        "file": "src/argus/driver/lib-driver-resume.sh",
        "find": '  if [ "$HALT_REC_PHASE" = "$number" ] && [ -n "$record_identity" ] \\\n',
        "replace": '  if [ -n "$record_identity" ] \\\n',
        "cases": "resume",  # selftest_case_resume cross-phase
        "verdict": "driver self-test:",
        "expect": "--resume N consumed another phase's record",
    },
    {
        "name": "resume-group-not-read",
        "breaks": "a resumed member of a reservation group cuts a branch of its own",
        "file": "src/argus/driver/lib-driver-resume.sh",
        "find": '  if [ "${work_kind:-phase}" != issue ] && ! group_take_declared "$number"; then\n',
        "replace": "  if false; then  # FAULT: the resume runs without the group\n",
        "cases": "group_resume",  # selftest_case_group_resume
        "verdict": "driver self-test:",
        "expect": "the resume cut a branch of its own for the group member",
    },
    {
        "name": "resume-fence-reply-not-parsed",
        "breaks": "the fence parser drops the piped reply",
        "file": "src/argus/driver/lib-driver-fence.sh",
        "find": "    data = json.load(sys.stdin)\n",
        "replace": "    data = {}  # FAULT: the piped reply is dropped\n",
        "cases": "resume",  # selftest_resume_fence_cases
        "verdict": "driver self-test:",
        "expect": "the real reserve reply was not parsed",
    },
    {
        "name": "abandon-orphan-check-skipped",
        "breaks": "abandon parks a phase with a live orphaned pid",
        "file": "src/argus/driver/lib-driver-abandon.sh",
        "find": "  driver_orphan_check || { printf 'argus abandon: a phase run is alive with no window (orphaned); not abandoning\\n' >&2; exit 1; }\n",
        "replace": "  true  # FAULT: the orphan check is skipped\n",
        "cases": "abandon",  # selftest_case_abandon
        "verdict": "driver self-test:",
        "expect": "abandon parked a phase with a live orphaned pid",
    },
    {
        "name": "abandon-skips-the-driver-lock",
        "breaks": "abandon runs without taking the shared driver lock",
        "file": "src/argus/driver/lib-driver-abandon.sh",
        "find": "  driver_lock_take || { printf 'argus abandon: another driver instance holds the lock; try again\\n' >&2; exit 1; }\n",
        "replace": "  true  # FAULT: the shared lock is not taken\n",
        "cases": "abandon",  # selftest_case_abandon lock case
        "verdict": "driver self-test:",
        "expect": "abandon ran while another driver held the lock",
    },
)
