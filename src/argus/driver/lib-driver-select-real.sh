#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Acquire a real selection after finished work has been reconciled.
#
_drive_real_selection() {
  selection="$(coordination_select)" || selection_rc=$?
  # The JSON state decides the result; exit 1 also means an empty queue.
  selection_state="$(printf '%s' "$selection" | selection_value state)"
  if [ -z "$selection_state" ]; then
    log "coordination state unavailable; refusing to launch (next_phase rc=$selection_rc)"
    { _DRIVE_RC=1; return 0; }
  fi
  bind_selection_kind "$selection"
  sync_branch_args
  local sync_rc=0
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode start \
      "${SYNC_BRANCH_ARGS[@]+"${SYNC_BRANCH_ARGS[@]}"}" \
      >"$RUN_DIR/coordination-sync.json" 2>/dev/null || sync_rc=$?
  # A sync that fails is a repair: keep the claim and retry the sync next tick.
  # The selection itself is already in hand. A degraded sync exits 0 and carries
  # its reason in the JSON, so the warning is read as well as the exit code.
  if [ "$sync_rc" -ne 0 ] \
      || grep -q '"warning"' "$RUN_DIR/coordination-sync.json" 2>/dev/null; then
    log "coordination checkout sync failed; keeping the claim, the next tick retries (see $RUN_DIR/coordination-sync.json)"
  fi
  return 0
}
