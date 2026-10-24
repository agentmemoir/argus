#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Per-case wall time for the driver self-test. Sourced by phase-driver.sh before
# lib-selftest.sh, which calls these functions.
#
# Each case's time prints when the case ends, and the slowest ten print once the
# run is over. Timing only reads the clock; no case runs differently for it.
#
# The lines start with "case time:", not "driver self-test:". The fault harness
# takes the first line that starts with "driver self-test:" as the verdict, so a
# timing line with that prefix would stand in for the real one.

SELFTEST_TIMINGS=""

# Seconds from a monotonic clock, to milliseconds. macOS `date` has no %N, and
# bash 3.2 has no EPOCHREALTIME, so this asks the interpreter the driver already
# runs on.
selftest_clock() {
  "${PYTHON:-python3}" -c 'import time; print(f"{time.monotonic():.3f}")'
}

# Record one finished case. $1 is its name, $2 the clock reading at its start.
selftest_record_case() {
  local name="$1" started="$2" secs
  secs="$(awk -v a="$started" -v b="$(selftest_clock)" 'BEGIN { printf "%.2f", b - a }')"
  SELFTEST_TIMINGS="${SELFTEST_TIMINGS}${secs} ${name}"$'\n'
  printf 'case time: %s %s s\n' "$name" "$secs"
}

# The slowest ten cases of this run, slowest first.
selftest_print_slowest() {
  [ -n "$SELFTEST_TIMINGS" ] || return 0
  printf 'case time: slowest 10 of this run\n'
  printf '%s' "$SELFTEST_TIMINGS" | sort -rn |
    awk 'NR <= 10 { printf "case time:   %8s s  %s\n", $1, $2 }'
}
