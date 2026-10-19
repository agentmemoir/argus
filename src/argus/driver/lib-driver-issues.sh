#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
# Source-specific binding and issue launch checks.

bind_selection_kind() {
  work_kind="$(printf '%s' "$1" | selection_value kind)"
  item_key=phase
  BOARD_KIND_ARGS=()
  GITSYNC_KIND_ARGS=()
  if [ "$work_kind" = issue ]; then
    item_key=issue
    BOARD_KIND_ARGS=(--issues)
    GITSYNC_KIND_ARGS=(--kind issue)
  else
    work_kind=phase
  fi
}

# Retained for a manual run; no tick calls it since issue intake was cut.
issue_preflight() {
  if ! command -v "$CMD_GH" >/dev/null 2>&1 \
      && { [ ! -f "$CMD_GH" ] || [ ! -x "$CMD_GH" ]; }; then
    PREFLIGHT_REASON="GitHub command is missing or not executable: $CMD_GH"
    return 1
  fi
  if [ -z "${BOARD_REPO:-}" ] || [ -z "${BOARD_FILE:-}" ] \
      || [ -z "${BOARD_ISSUES_FILE:-}" ]; then
    PREFLIGHT_REASON="issue work requires board.file and board.issues-file"
    return 1
  fi
  local reason
  if ! reason="$("$PYTHON" - "$REPO" "${ISSUES_TASK_DIR:-}" <<'PY'
import os
import sys
from pathlib import Path
root, folder = (Path(value).resolve() for value in sys.argv[1:])
if not folder.is_relative_to(root):
    print(f"issues.task-dir is outside the checkout: {folder}")
    raise SystemExit(1)
if not folder.is_dir() or not os.access(folder, os.R_OK | os.W_OK | os.X_OK):
    print(f"issues.task-dir must be a readable, writable folder: {folder}")
    raise SystemExit(1)
PY
  )"; then
    PREFLIGHT_REASON="$reason"
    return 1
  fi
  if [ -n "${ISSUES_CODEOWNERS_FILE:-}" ] && [ ! -r "$ISSUES_CODEOWNERS_FILE" ]; then
    PREFLIGHT_REASON="issues.codeowners-file is unreadable: $ISSUES_CODEOWNERS_FILE"
    return 1
  fi
}

report_issue_selection() {
  [ "${work_kind:-phase}" = issue ] || return 0
  local line
  while IFS= read -r line; do
    log "$line"
  done < <(printf '%s' "$1" | "$PYTHON" -c '
import json, sys
result = json.load(sys.stdin)
print("issue selection: preference={} chosen={} sort={} candidates={}".format(
    result.get("preference", ""), result.get("issue", "none"),
    result.get("sort", ""), len(result.get("candidates", []))))
trust = result.get("trust", {})
print("issue trust: " + " ".join(f"{key}={trust.get(key, 0)}" for key in ("authors", "apps", "associations", "codeowners")))
for entry in trust.get("unresolved", []):
    print("issue trust unresolved: " + entry)
folder = result.get("task_dir", {})
print("issue task folder: " + " ".join(f"{key}={int(folder.get(key, False))}" for key in ("exists", "writable", "inside_checkout")))
for reason, count in sorted(result.get("skipped", {}).items()):
    if count:
        print(f"issues skipped: {reason}={count}")
if result.get("truncated"):
    print("issue candidates were capped at {}; raise issues.list-limit to see more".format(result.get("list_limit", "?")))
if result.get("assignment_error"):
    print("issue assignment failed; the board claim is held and work can start")
' 2>/dev/null)
  if [ -n "$(printf '%s' "$1" | selection_value assignment_error)" ]; then
    notify "Issue assignment failed. The board claim is held; the run will start."
  fi
}

issue_announce() {
  [ "${WORK_SOURCE:-phases}" = issues ] || return 0
  [ "${ISSUES_ANNOUNCE:-1}" = 1 ] || return 0
  [ -n "$1" ] || return 0
  "$PYTHON" "$ARGUS/issues/lifecycle.py" announce \
    "${ARGUS_CONFIG_ARGS[@]}" --issue "$1" --event "$2" \
    >"$RUN_DIR/issue-notice-$1.json" 2>&1 \
    || log "issue $1: could not post the $2 notice; see $RUN_DIR/issue-notice-$1.json"
  return 0
}
