#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: the CI repair round on a red pull request.
#
# The real repair_tick and repair_cli.py run against a stub gh that reports one
# failed check. tmux and notify are stubbed, so the case reads the launch result
# and the notices without a phase window. It runs in a subshell, so no stub and
# no setting reaches the next case.

selftest_case_repair_round() {
  ( selftest_repair_round_body ) || fail=1
}

# The number of counted rounds in the record of pull request 77.
selftest_repair_count() {
  "$PYTHON" -c 'import json, sys
try:
    print(len(json.load(open(sys.argv[1])).get("attempts", [])))
except FileNotFoundError:
    print(0)' "$RUN_DIR/repair-77.json"
}

selftest_repair_round_body() {
  local bad=0 pr=https://github.com/acme/widget/pull/77 branch=feature/repair-red
  local stub cfg pending wt run_dir notes main_branch
  unset DRIVER_STUB_COORDINATION
  rm -f "$HALT" "$RUN_DIR"/repair-77.*
  stub="$RUN_DIR/repair-gh"
  cat >"$stub" <<'SH'
#!/bin/sh
if [ "$1 $2" = "pr view" ]; then echo '{"mergeable":"MERGEABLE"}'; exit 0; fi
echo '[{"name":"ci","bucket":"fail","state":"FAILURE","link":"https://example/check/1"}]'
exit 1
SH
  chmod 700 "$stub"
  cfg="$RUN_DIR/repair-config.json"
  "$PYTHON" - "$ARGUS_CONFIG_FILE" "$cfg" "$stub" <<'PY' || return 1
import json, sys
doc = json.load(open(sys.argv[1]))
doc["commands"]["gh"] = sys.argv[3]
open(sys.argv[2], "w").write(json.dumps(doc))
PY
  run_dir="$RUN_DIR"
  load_fixture_config "$cfg"
  RUN_DIR="$run_dir"
  REPAIR_PIPELINE="$RUN_DIR/repair-stub.yaml"; : >"$REPAIR_PIPELINE"
  REPAIR_ATTEMPTS=2; REPAIR_WAIT=0
  notes="$RUN_DIR/repair-notes"; : >"$notes"
  notify() { printf '%s\n' "$1" >>"$notes"; }
  tmux_session_ensure() { return 0; }
  tmux_set_title() { :; }
  tmux_phase_window_select() { :; }
  tmux_phase_window_open() { return 1; }
  git -C "$REPO" push -q origin "HEAD:refs/heads/$branch" || return 1
  main_branch="$(git -C "$REPO" symbolic-ref -q HEAD || echo detached)"
  pending="{\"awaiting_review\":[{\"phase\":\"100010\",\"branch\":\"$branch\",\"pull_request\":\"$pr\"}]}"
  wt="$(repair_worktree 77)"

  PENDING_PUBLISHED=0
  repair_tick "$pending"
  if [ "$(selftest_repair_count)" = 1 ] && [ "$PENDING_PUBLISHED" = 0 ] && [ ! -e "$wt" ] \
      && [ -f "$RUN_DIR/repair-77.refused" ] && [ ! -s "$notes" ]; then
    echo "ok: repair: a refused launch leaves a refusal for the next tick, sends no notice, and leaves no worktree"
  else
    echo "FAIL: repair: a refused launch left a worktree, sent a notice, or left no refusal (count $(selftest_repair_count))"; bad=1
  fi
  : >"$notes"

  tmux_phase_window_open() { return 0; }
  PENDING_PUBLISHED=0
  repair_tick "$pending"
  if [ "$(selftest_repair_count)" = 1 ] && [ "$PENDING_PUBLISHED" = 1 ] && [ ! -f "$RUN_DIR/repair-77.refused" ] \
      && [ "$(git -C "$wt" rev-parse HEAD 2>/dev/null)" = "$(git -C "$REPO" rev-parse "origin/$branch")" ] \
      && ! git -C "$wt" symbolic-ref -q HEAD >/dev/null \
      && [ "$(git -C "$REPO" symbolic-ref -q HEAD || echo detached)" = "$main_branch" ]; then
    echo "ok: repair: the refused launch is taken back, and a red pull request starts one counted round in a detached worktree"
  else
    echo "FAIL: repair: the refused launch was not taken back, or the round was not counted once (count $(selftest_repair_count), started $PENDING_PUBLISHED)"; cat "$notes"; bad=1
  fi

  repair_tick "$pending"
  repair_tick "$pending"
  if [ "$(wc -l <"$notes" | tr -d ' ')" = 1 ] && grep -q "$pr" "$notes" && [ ! -e "$wt" ]; then
    echo "ok: repair: after repair.attempts rounds one notice is sent and the worktree is gone"
  else
    echo "FAIL: repair: expected one escalation notice and no worktree"; cat "$notes"; bad=1
  fi

  selftest_repair_issue_pr || bad=1
  selftest_repair_backup || bad=1
  selftest_repair_fresh_round || bad=1
  selftest_repair_refused_budget base || bad=1
  selftest_repair_refused_budget glide || bad=1

  git -C "$REPO" push -q origin --delete "$branch" || true
  rm -f "$stub" "$cfg" "$notes" "$REPAIR_PIPELINE" "$RUN_DIR"/repair-77.*
  return "$bad"
}

# One `issues` pull request: the issues tick lists it, starts one round, and the
# round's glide sees the configuration file only through the driver's launch.
selftest_repair_issue_pr() {
  local bad=0 pr=https://github.com/acme/widget/pull/78 branch=issue/78-fix-ci glide seen
  git -C "$REPO" push -q origin "HEAD:refs/heads/$branch" || return 1
  issue_review_snapshot() {
    printf '{"awaiting_review":[{"issue":78,"branch":"%s","pull_request":"%s"}]}\n' "$branch" "$pr"
  }
  WORK_SOURCE=issues
  reconcile_pending_reviews
  if [ "$PENDING_PUBLISHED" = 1 ] && [ -d "$(repair_worktree 78)" ] \
      && grep -q '"attempts": \[[0-9]' "$RUN_DIR/repair-78.json"; then
    echo "ok: repair: an issue pull request on a recorded branch starts one counted round"
  else
    echo "FAIL: repair: the issues tick did not start a round for the issue pull request"; bad=1
  fi
  glide="$RUN_DIR/repair-glide"; seen="$RUN_DIR/repair-glide-env"
  printf '#!/bin/sh\nprintf "%%s\\n" "$ARGUS_CONFIG_FILE" >"%s"\nexit 1\n' "$seen" >"$glide"
  chmod 700 "$glide"
  ( export -n ARGUS_CONFIG_FILE; CMD_GLIDE="$glide"; PIDFILE="$RUN_DIR/repair-pid"
    run_repair_session 78 "$RUN_DIR/repair-78.md" "$branch" ) >/dev/null 2>&1
  if [ -n "$ARGUS_CONFIG_FILE" ] && [ "$(cat "$seen" 2>/dev/null)" = "$ARGUS_CONFIG_FILE" ] \
      && [ ! -e "$(repair_worktree 78)" ]; then
    echo "ok: repair: the round's glide gets ARGUS_CONFIG_FILE and the worktree is removed"
  else
    echo "FAIL: repair: glide saw '$(cat "$seen" 2>/dev/null)', not '$ARGUS_CONFIG_FILE'"; bad=1
  fi
  git -C "$REPO" push -q origin --delete "$branch" || true
  rm -f "$glide" "$seen" "$RUN_DIR"/repair-78.* "$RUN_DIR/repair-pid"
  return "$bad"
}

# A push the remote refuses, because the branch moved during the round, keeps the
# agent's commit and its uncommitted edits on a backup ref (hard line 2).
selftest_repair_backup() {
  local bad=0 branch=issue/79-moved glide notes ref moved
  notes="$RUN_DIR/repair-backup-notes"; : >"$notes"
  notify() { printf '%s\n' "$1" >>"$notes"; }
  git -C "$REPO" push -q origin "HEAD:refs/heads/$branch" || return 1
  repair_worktree_add 79 "$branch" || return 1
  moved="$(git -C "$REPO" -c user.name=t -c user.email=t@t commit-tree 'HEAD^{tree}' -p HEAD -m moved)"
  git -C "$REPO" push -q origin "$moved:refs/heads/$branch" || return 1
  glide="$RUN_DIR/repair-glide-commit"
  printf '#!/bin/sh\ngit -c user.name=t -c user.email=t@t commit -q --allow-empty -m agent\necho edit >repair-edit.txt\n' >"$glide"
  chmod 700 "$glide"
  # The base check has its own cases. Here the branch moves on purpose, so the
  # check would refuse the round before the agent runs; this case stubs it clean.
  ( base_sync_repair() { BASE_SYNC_STATE=clean BASE_SYNC_NOTE=""; }
    CMD_GLIDE="$glide"; PIDFILE="$RUN_DIR/repair-pid"
    run_repair_session 79 "$RUN_DIR/repair-79.md" "$branch" ) >/dev/null 2>&1
  ref="$(git -C "$REPO" for-each-ref --format='%(refname)' 'refs/argus/repair-backup/79-*' | head -1)"
  if [ -n "$ref" ] && [ ! -e "$(repair_worktree 79)" ] && grep -qF "$ref" "$notes" \
      && git -C "$REPO" cat-file -e "$ref:repair-edit.txt" \
      && [ "$(git -C "$REPO" log -1 --format=%s "$ref^")" = agent ]; then
    echo "ok: repair: a refused push keeps the commit and edits on a named backup ref"
  else
    echo "FAIL: repair: a refused push lost the work (ref '$ref')"; cat "$notes"; bad=1
  fi
  [ -z "$ref" ] || git -C "$REPO" update-ref -d "$ref"
  git -C "$REPO" push -q origin --delete "$branch" || true
  rm -f "$glide" "$notes" "$RUN_DIR"/repair-79.* "$RUN_DIR/repair-pid"
  return "$bad"
}

# Two rounds on one pull request. The first round's run folder is left completed.
# The stub is glide's behavior: it refuses a finished run unless --fresh is passed,
# and archives the folder on --fresh. A round that reaches the stub writes one line.
selftest_repair_fresh_round() {
  local bad=0 branch=issue/80-fresh glide agent round
  export RUN_DIR
  glide="$RUN_DIR/repair-glide-fresh"; agent="$RUN_DIR/repair-80-agent.log"
  rm -rf "$RUN_DIR/repair-80" "$RUN_DIR"/repair-80.prev-* "$agent"
  git -C "$REPO" push -q origin "HEAD:refs/heads/$branch" || return 1
  cat >"$glide" <<'SH'
#!/bin/sh
folder="$RUN_DIR/repair-80"
fresh=0
for arg in "$@"; do [ "$arg" = --fresh ] && fresh=1; done
if [ -f "$folder/run.json" ] && [ "$fresh" = 0 ]; then
  echo "run already finished as completed; --fresh to start over" >&2
  exit 2
fi
if [ "$fresh" = 1 ] && [ -d "$folder" ]; then mv "$folder" "$folder.prev-$$"; fi
mkdir -p "$folder"
printf '{"state": "completed"}\n' >"$folder/run.json"
echo "agent ran" >>"$RUN_DIR/repair-80-agent.log"
SH
  chmod 700 "$glide"
  for round in 1 2; do
    repair_worktree_add 80 "$branch" || return 1
    ( base_sync_repair() { BASE_SYNC_STATE=clean BASE_SYNC_NOTE=""; }
      CMD_GLIDE="$glide"; PIDFILE="$RUN_DIR/repair-pid"
      run_repair_session 80 "$RUN_DIR/repair-80.md" "$branch" ) >/dev/null 2>&1
  done
  if [ "$(wc -l <"$agent" 2>/dev/null | tr -d ' ')" = 2 ] && [ ! -e "$(repair_worktree 80)" ]; then
    echo "ok: repair: a second round after a completed round reaches the agent launch"
  else
    echo "FAIL: repair: the second round did not reach the agent (agent runs: $(cat "$agent" 2>/dev/null | wc -l | tr -d ' '))"; bad=1
  fi
  git -C "$REPO" push -q origin --delete "$branch" || true
  rm -rf "$RUN_DIR/repair-80" "$RUN_DIR"/repair-80.prev-* "$agent"
  rm -f "$glide" "$RUN_DIR/repair-pid"
  return "$bad"
}

# Launches the session refuses before any agent runs. Mode base: the base check fails.
# Mode glide: glide exits 2 and writes no run marker. Neither spends a round. The third
# tick sends one Needs You that gives both counts and the reason, and no more for this head.
selftest_repair_refused_budget() {
  local mode="$1" bad=0 n pr branch glide reason pending tick
  if [ "$mode" = glide ]; then
    n=82; reason="glide exited with rc=2 before it wrote a run marker"
  else
    n=81; reason="the base check failed: base-sync: configuration error: bad task dir"
  fi
  pr=https://github.com/acme/widget/pull/$n; branch=issue/$n-refused
  glide="$RUN_DIR/repair-$n-glide"
  refused_notes="$RUN_DIR/repair-refused-notes"; : >"$refused_notes"
  notify() { printf '%s\n' "$1" >>"$refused_notes"; }
  tmux_phase_window_open() { return 0; }
  rm -f "$RUN_DIR"/repair-$n.* "$glide"
  printf '#!/bin/sh\nexit 2\n' >"$glide"; chmod 700 "$glide"
  git -C "$REPO" push -q origin "HEAD:refs/heads/$branch" || return 1
  pending="{\"awaiting_review\":[{\"phase\":\"100011\",\"branch\":\"$branch\",\"pull_request\":\"$pr\"}]}"
  for tick in 1 2 3; do
    PENDING_PUBLISHED=0
    repair_tick "$pending"
    if [ "$PENDING_PUBLISHED" = 1 ]; then
      # The launched round's session runs here, and fails the way this mode says.
      ( base_sync_repair() {
          if [ "$mode" = glide ]; then BASE_SYNC_STATE=clean BASE_SYNC_NOTE=""; return 0; fi
          BASE_SYNC_STATE=failed BASE_SYNC_NOTE=""
          echo "base-sync: configuration error: bad task dir" >"$RUN_DIR/repair-$n.base"
        }
        CMD_GLIDE="$glide"; PIDFILE="$RUN_DIR/repair-pid"
        run_repair_session "$n" "$RUN_DIR/repair-$n.md" "$branch" ) >/dev/null 2>&1
    fi
  done
  repair_tick "$pending"
  if [ "$(wc -l <"$refused_notes" | tr -d ' ')" = 1 ] \
      && grep -qF "0 repair round(s) ran and 2 launch(es) refused" "$refused_notes" \
      && grep -qF "The last refusal: $reason." "$refused_notes" \
      && [ "$("$PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("attempts", [])))' "$RUN_DIR/repair-$n.json")" = 0 ]; then
    echo "ok: repair: $mode refused launches spend no round and end in one Needs You with both counts and the reason"
  else
    echo "FAIL: repair: $mode refused launches did not end in one Needs You with the real counts and reason"; cat "$refused_notes"; bad=1
  fi
  git -C "$REPO" push -q origin --delete "$branch" || true
  rm -f "$refused_notes" "$glide" "$RUN_DIR"/repair-$n.* "$RUN_DIR/repair-pid"
  return "$bad"
}
