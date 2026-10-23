# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The repair round checks its branch against the base before the agent starts."""

import shlex
from pathlib import Path

from driver_maintenance_support import shell

# PYTHON and CMD_GLIDE name shell functions, so the driver calls them the way it
# calls the real CLI and the agent run. The base stub logs each argument on its
# own line and prints and returns the outcome the test sets.
STUBS = """
tmux_set_title() { :; }
PYTHON=base_stub
CMD_GLIDE=glide_stub
CMD_GIT=git
ARGUS_CONFIG_FILE=fixture.json
REPAIR_PIPELINE=repair.yaml
glide_stub() { log "GLIDE $*"; }
base_stub() {
  local a
  log "BASE_SYNC"
  for a in "$@"; do log "ARG $a"; done
  printf '%s\\n' "$BASE_OUT"
  return "$BASE_RC"
}
"""


def _round(tmp_path: Path, base_rc: int, base_out: str, branch: str) -> list[str]:
    body = (
        STUBS
        + f"""
BASE_RC={base_rc}
BASE_OUT={shlex.quote(base_out)}
mkdir -p "$RUN_DIR/repair-worktree-k1"
( run_repair_session k1 "fix the checks" {shlex.quote(branch)} ) || true
cat "$RUN_DIR/events"
"""
    )
    return shell(tmp_path, body).splitlines()


def _agent_lines(events: list[str]) -> list[str]:
    return [line for line in events if line.startswith("GLIDE ")]


def test_round_checks_the_base_before_the_agent_starts(tmp_path: Path) -> None:
    events = _round(tmp_path, 0, "base-sync: no conflict with master", "feature/x")
    assert "BASE_SYNC" in events
    assert events.index("BASE_SYNC") < events.index(_agent_lines(events)[0])


def test_text_conflict_is_handed_to_the_agent(tmp_path: Path) -> None:
    events = _round(
        tmp_path,
        3,
        "base-sync: text conflict with origin/master; resolve it, then commit and push",
        "feature/x",
    )
    agent = _agent_lines(events)
    assert len(agent) == 1
    assert "stopped on text conflicts" in agent[0]


def test_failed_base_check_runs_no_agent(tmp_path: Path) -> None:
    events = _round(
        tmp_path, 1, "base-sync: failed: git fetch origin failed", "feature/x"
    )
    assert _agent_lines(events) == []
    assert "repair k1: base-sync: failed: git fetch origin failed" in events
    assert "repair k1: the base check failed, so this round ran no agent" in events


def test_branch_is_one_argument_even_when_hostile(tmp_path: Path) -> None:
    name = "x;touch MARKER"
    events = _round(tmp_path, 0, "base-sync: no conflict with master", name)
    assert f"ARG {name}" in events
