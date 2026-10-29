#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Acquire a real selection after finished work has been reconciled.
#
# Needs You notices the issue selector raised on this tick. The selector sends each
# one as a key and one line of text. The text last sent for a key is kept in the run
# folder, so a later tick that holds the same state sends nothing.
report_issue_notices() {
  local key text marker
  while IFS=$'\t' read -r key text; do
    [ -n "$key" ] || continue
    marker="$RUN_DIR/log-last-notice-$key"
    if [ "$(cat "$marker" 2>/dev/null)" = "$text" ]; then continue; fi
    notify "$text"
    printf '%s' "$text" >"$marker"
  done < <(printf '%s' "$1" | "$PYTHON" -c '
import json, sys
for entry in json.load(sys.stdin).get("notices", []) or []:
    print("\t".join([str(entry.get("key") or ""), str(entry.get("text") or "")]))
' 2>/dev/null)
}

_drive_real_selection() {
  selection="$(coordination_select)" || selection_rc=$?
  # The JSON state decides the result; exit 1 also means an empty queue.
  selection_state="$(printf '%s' "$selection" | selection_value state)"
  if [ -z "$selection_state" ]; then
    log "coordination state unavailable; refusing to launch (next_phase rc=$selection_rc)"
    { _DRIVE_RC=1; return 0; }
  fi
  bind_selection_kind "$selection"
  report_issue_notices "$selection"
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
