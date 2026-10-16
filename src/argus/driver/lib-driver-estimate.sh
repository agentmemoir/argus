#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Estimate factor: after a phase finishes, move its developer model's factor
# toward the wall time the phase took (see ../estimate.py). It is off unless the
# configuration names `estimate.factors-file` and `estimate.ideal-days`, and it
# never stops the line: every outcome is one log line.
# Sourced by phase-driver.sh; not executable on its own.
#
estimate_update() {   # <number> <rel> <started epoch> <resumed: 0|1>
  local line
  line="$("$PYTHON" - "$ARGUS" "$1" "$(phase_run_dir "$1" "$2")" \
    "$(($(date +%s) - $3))" "$4" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap  # noqa: F401
from argus import conventions
conventions.configure_from_cli(sys.argv[6:])
from argus.estimate import update
print(update(int(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4]), resumed=sys.argv[5] == "1"))
PY
)" || line="estimate: phase $1 factor update failed"
  [ -z "$line" ] || log "$line"
  return 0
}
