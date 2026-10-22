#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# CI repair rounds for pull requests that wait on review.
#
# Each tick, before new work, gitsync/repair_cli.py reads the checks of every
# awaiting-review pull request and decides. A "repair" decision starts one run of
# `repair.pipeline` in the phase window, so one active run stays the rule. The
# run works in its own temporary git worktree on the pull request's branch: the
# main checkout never moves. The session pushes the agent's commit to that
# branch only (never force) and removes the worktree when it ends. A worktree
# that a crash left behind is removed on the next tick.

repair_worktree() { printf '%s/repair-worktree-%s' "$RUN_DIR" "$1"; }

# Remove one repair worktree. Edits or commits that never reached the remote are
# named in a notice first, so a removal is never silent about lost work.
repair_worktree_remove() {
  local wt="$1" left
  left="$("$CMD_GIT" -C "$wt" status --porcelain 2>/dev/null | head -1 || true)"
  left="$left$("$CMD_GIT" -C "$wt" log --oneline --not --remotes 2>/dev/null | head -1 || true)"
  [ -z "$left" ] || notify "Repair worktree $wt held edits or commits that were not pushed. Argus removed it; the next round starts from the pull request branch."
  "$CMD_GIT" -C "$REPO" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  "$CMD_GIT" -C "$REPO" worktree prune >/dev/null 2>&1 || true
}

# A tick runs this step only while the phase window is free, so no repair session
# is alive and every repair worktree left here is stale.
repair_worktree_prune() {
  local wt
  for wt in "$RUN_DIR"/repair-worktree-*; do
    [ -e "$wt" ] || continue
    log "removing a stale repair worktree: $wt"
    repair_worktree_remove "$wt"
  done
}

# Create the worktree for one round, detached at the fetched pull request branch.
# A detached HEAD lets the round start even when another checkout has the branch.
# The repair pipeline keeps its run folder under the driver's runs folder, so
# when that folder is inside the checkout the worktree gets a link to its top
# directory. The link must be ignored by Git, or the round does not start.
repair_worktree_add() {
  local key="$1" branch="$2" wt top
  wt="$(repair_worktree "$key")"
  "$CMD_GIT" -C "$REPO" fetch --quiet origin \
    "+refs/heads/$branch:refs/remotes/origin/$branch" >/dev/null 2>&1 \
    || { log "repair $key: could not fetch $branch; the next tick retries"; return 1; }
  "$CMD_GIT" -C "$REPO" worktree add --quiet --detach "$wt" "origin/$branch" >/dev/null 2>&1 \
    || { log "repair $key: could not add a worktree for $branch; the next tick retries"; return 1; }
  case "$WF" in
    "$REPO"/*)
      top="${WF#"$REPO"/}"; top="${top%%/*}"
      if [ ! -e "$wt/$top" ]; then
        ln -s "$REPO/$top" "$wt/$top"
        if ! "$CMD_GIT" -C "$wt" check-ignore -q "$top"; then
          notify "Repair $key did not start: Git does not ignore '$top' in the checkout, so its link could be committed."
          repair_worktree_remove "$wt"
          return 1
        fi
      fi ;;
  esac
}

repair_launch() {
  local key="$1" task="$2" branch="$3" launch quoted setting
  repair_worktree_add "$key" "$branch" || return 1
  printf -v launch 'bash %q' "$SELF"
  for setting in "${ARGUS_CONFIG_ARGS[@]}"; do
    printf -v quoted ' %q' "$setting"
    launch="$launch$quoted"
  done
  printf -v quoted ' --repair-session %q %q %q' "$key" "$task" "$branch"
  launch="$launch$quoted"
  9>&- tmux_session_ensure \
    || { notify "repair $key failed to create tmux session '$SESSION'"; repair_worktree_remove "$(repair_worktree "$key")"; return 1; }
  tmux_set_title "repair $key starting"
  9>&- tmux_phase_window_open "$launch" "$RUN_DIR/session-repair-$key.log" \
    || { notify "repair $key failed to launch in tmux"; repair_worktree_remove "$(repair_worktree "$key")"; return 1; }
  tmux_phase_window_select
  log "launched repair $key in tmux '$SESSION' window 'phase'"
}

# The tick's repair step. $1 is the pending-review snapshot. A launched round
# sets PENDING_PUBLISHED, the tick's "no new work" signal, so the repair runs
# before any new work.
repair_tick() {
  local rows pr branch key out rc
  [ -n "${REPAIR_PIPELINE:-}" ] || return 0
  [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 0
  [ ! -f "$HALT" ] || return 0
  repair_worktree_prune
  # A missing pipeline would spend every round and end in a false escalation.
  if [ ! -r "$REPAIR_PIPELINE" ]; then
    log_changed repair-pipeline "repair: cannot read $REPAIR_PIPELINE; no repair round starts"
    return 0
  fi
  rows="$(printf '%s' "$1" | "$PYTHON" -c 'import json, sys
for row in json.load(sys.stdin).get("awaiting_review", []):
    if row.get("pull_request") and row.get("branch"):
        print(row["pull_request"], row["branch"])')" || return 0
  while read -r pr branch; do
    [ -n "$pr" ] || continue
    key="${pr%/}"; key="${key##*/}"
    rc=0
    out="$("$PYTHON" "$ARGUS/gitsync/repair_cli.py" "${ARGUS_CONFIG_ARGS[@]}" \
      --pull-request "$pr" --run-dir "$RUN_DIR" \
      --attempts "$REPAIR_ATTEMPTS" --wait "$REPAIR_WAIT" </dev/null)" || rc=$?
    if [ "$rc" -ne 0 ]; then
      log "repair: the checks of $pr could not be read (rc=$rc); the next tick retries"
      continue
    fi
    case "$(selection_value action <<<"$out")" in
      escalate) notify "$(selection_value message <<<"$out")" ;;
      repair)
        repair_launch "$key" "$(selection_value task <<<"$out")" "$branch" </dev/null || continue
        "$PYTHON" "$ARGUS/gitsync/repair_cli.py" "${ARGUS_CONFIG_ARGS[@]}" \
          --pull-request "$pr" --run-dir "$RUN_DIR" --launched \
          --attempts "$REPAIR_ATTEMPTS" --wait "$REPAIR_WAIT" </dev/null >/dev/null \
          || log "repair $key: the round started but was not counted"
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        return 0 ;;
    esac
  done <<<"$rows"
}

# One repair round, inside the phase window. It runs the repair pipeline in the
# round's worktree, pushes the agent's commit to the pull request branch (never
# force; a moved branch rejects the push), and removes the worktree.
run_repair_session() {
  local key="$1" task="$2" branch="$3" wt rc=0
  wt="$(repair_worktree "$key")"
  trap 'rm -f "$PIDFILE"; exit 130' HUP INT TERM
  printf '%s\n' "$$" >"$PIDFILE"
  tmux_set_title "repair $key running"
  if ! cd "$wt"; then
    log "repair $key: its worktree is missing; the round ran nothing"
  else
    "$CMD_GLIDE" run --pipeline "$REPAIR_PIPELINE" \
      --input "repair_id=$key" --input "repair_task=$task" || rc=$?
    if [ "$rc" -ne 0 ]; then
      log "repair $key: the run ended with rc=$rc and pushed nothing"
    elif [ -z "$("$CMD_GIT" -C "$wt" log --oneline --not --remotes | head -1)" ]; then
      log "repair $key: the run made no commit; nothing pushed to $branch"
    elif "$CMD_GIT" -C "$wt" push --quiet origin "HEAD:refs/heads/$branch" >/dev/null 2>&1; then
      log "repair $key: pushed to $branch; the next tick reads the new checks"
    else
      log "repair $key: could not push to $branch (rc=$?)"
    fi
    cd "$REPO" || true
    repair_worktree_remove "$wt"
  fi
  tmux_set_title "repair $key done (rc=$rc)"
  rm -f "$PIDFILE"
  exit 0
}
