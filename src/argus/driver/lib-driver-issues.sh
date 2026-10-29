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

# The run directory of the work this tick runs. A given file is used as it is.
# An issue run given no file looks up its frozen task file in issues.task-dir.
# With none there, it logs one line naming issue_file and prints nothing: the
# caller must not guess a folder.
run_dir_of() {   # <number> <rel>
  local number="$1" rel="$2" file
  if [ "${work_kind:-phase}" = issue ] && [ -z "$rel" ]; then
    file="$(issue_task_file_of "$number")"
    if [ -z "$file" ]; then
      log "issue $number has no task file; the pipeline input ${PHASE_FILE_INPUT:-issue_file} is missing; no run directory"
      return 0
    fi
    rel="$file"
  fi
  phase_run_dir "$number" "$rel"
}

# Render an issue's task file again with the first-selection renderer. Prints its
# path relative to the checkout; prints nothing when the render fails, and the
# reason goes to stderr.
issue_rerender() {   # <number>
  "$PYTHON" - "$ARGUS" "$REPO" "$1" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[4:])
from argus.issues.rerender import rerender_task
try:
    path = rerender_task(sys.argv[2], int(sys.argv[3]))
except Exception as exc:
    print(f"issue {sys.argv[3]}: task file render failed: {exc}", file=sys.stderr)
else:
    print(path.resolve().relative_to(Path(sys.argv[2]).resolve()).as_posix())
PY
}

# The task file an issue tick launches from. A missing file is rendered again;
# when the render also fails, the claim goes back to the queue so the next tick
# can move on. Prints the path, or nothing when the claim was returned.
issue_task_for_tick() {   # <number> <reservation id> <generation>
  local number="$1" file
  file="$(issue_task_file_of "$number")"
  if [ -n "$file" ]; then
    printf '%s\n' "$file"
    return 0
  fi
  file="$(issue_rerender "$number" 2>"$RUN_DIR/rerender-$number.err")"
  if [ -n "$file" ]; then
    log "issue $number: task file missing; re-rendered"
    printf '%s\n' "$file"
    return 0
  fi
  log "issue $number: task file missing; re-render failed (see $RUN_DIR/rerender-$number.err)"
  release_refused_claim "$number" "$2" "$3" task-file-missing
  return 0
}

issue_preflight() {
  if ! command -v "$CMD_GH" >/dev/null 2>&1 \
      && { [ ! -f "$CMD_GH" ] || [ ! -x "$CMD_GH" ]; }; then
    PREFLIGHT_REASON="GitHub command is missing or not executable: $CMD_GH"
    return 1
  fi
  if [ -z "${BOARD_REPO:-}" ] || [ -z "${BOARD_FILE:-}" ] \
      || [ -z "${BOARD_ISSUES_FILE:-}" ]; then
    PREFLIGHT_REASON="issue work requires board.repo, board.file, and board.issues-file"
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
' 2>/dev/null)
}

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
