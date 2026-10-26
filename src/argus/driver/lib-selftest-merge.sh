#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: the merge check, awaiting review, orphans, geometry, and hooks.
#
selftest_case_merge_orphan() {
  # The merge check runs on every selection, before it is acted on, because the
  # selector no longer stops on an open pull request and this is the only place
  # left that would turn a merged pull request into a finished phase. Driven
  # through the coordination stub so the case runs with no board and no network.
  local old_stub_sleep="$DRIVER_STUB_SLEEP"
  log "self-test: merge check with a merged and an unmerged pull request"
  # The case counts how many times the tick logs a waiting phase, and a tick
  # logs a line that has not changed since the last one only once.
  rm -f "$RUN_DIR"/log-last-*
  before_merged="$(grep -c 'phase 100010 is merged' "$LOG" || true)"
  before_waited="$(grep -c 'phase 100011 still waits' "$LOG" || true)"
  before_health="$(grep -c 'merge check: UNAVAILABLE' "$LOG" || true)"
  DRIVER_STUB_COORDINATION=review DRIVER_STUB_MERGED_PHASE=100010 \
    drive --self-test || true
  sleep 0.3
  merged_now="$(grep -c 'phase 100010 is merged' "$LOG" || true)"
  waited_now="$(grep -c 'phase 100011 still waits' "$LOG" || true)"
  health_now="$(grep -c 'merge check: UNAVAILABLE' "$LOG" || true)"
  if [ "$merged_now" -gt "$before_merged" ]; then
    echo "ok: a merged pull request is confirmed published from the maintenance pass (merged $before_merged -> $merged_now)"
  else
    echo "FAIL: the merge check never confirmed a merged pull request (merged stayed $before_merged)"; fail=1
  fi
  if [ "$waited_now" -gt "$before_waited" ] && [ "$health_now" -gt "$before_health" ]; then
    echo "ok: an unmerged pull request is reported without blocking the line, and a degraded merge check says so (waited $before_waited -> $waited_now, degraded $before_health -> $health_now)"
  else
    echo "FAIL: an unmerged pull request or a degraded merge check was not reported (waited $waited_now, degraded $health_now)"; fail=1
  fi
  unset DRIVER_STUB_COORDINATION DRIVER_STUB_MERGED_PHASE
  wait_phase_window_gone
  sleep 0.3

  # A selector that stops on an open pull request is a version disagreement
  # between this script and next_phase.py. It used to log and wait; it now
  # halts, which is right in principle but is a behaviour cliff, so it is
  # pinned here rather than left to production.
  log "self-test: selector still reports AWAITING_REVIEW"
  rm -f "$HALT"
  DRIVER_STUB_COORDINATION=awaiting DISABLE_NOTIFY=1 drive --self-test || true
  sleep 0.3
  if grep -q "the selector should skip an open pull request" "$LOG"; then
    echo "ok: a selector that stops on an open pull request is named, not worked around"
  else
    echo "FAIL: the driver did not notice the selector disagreeing with it"; fail=1
  fi
  unset DRIVER_STUB_COORDINATION DISABLE_NOTIFY
  wait_phase_window_gone
  sleep 0.3

  # Orphan: killing the phase window does not signal the pane, so a live
  # recorded PID with no phase window must halt instead of starting a second
  # concurrent run. The session deliberately SURVIVES the kill here, which is
  # what makes this the real test: a session-scoped existence check would see a
  # live session, wrongly conclude work is in flight, and skip.
  rm -f "$HALT"
  # The stub waits for this file rather than sleeping a span the test guesses at.
  # The check needs a recorded PID that is still alive after the phase window is
  # killed, and a stub that finished on its own timer leaves nothing alive to
  # find, which reads as "orphan not detected" on a driver that is working.
  orphan_release="$tmp/orphan-release"
  orphan_ready="$tmp/orphan-ready"
  rm -f "$orphan_release" "$orphan_ready"
  DRIVER_STUB_RC=0
  DRIVER_STUB_WAIT_FILE="$orphan_release"
  DRIVER_STUB_READY_FILE="$orphan_ready"
  export DRIVER_STUB_RC DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  # On the stub, for the same reason as the cases at the top of this function: a
  # tick with no stub needs a real phase to launch, and reaching a launch with no
  # stub puts this machine's real public checkout on that phase's branch. This case
  # needs a live phase with a recorded pid, which is what the stub provides, and it
  # is the second place that did it before the stub was added here.
  DRIVER_STUB_COORDINATION=fence RECOVER_OWN_CLAIM=1
  export DRIVER_STUB_COORDINATION RECOVER_OWN_CLAIM
  drive --self-test || true
  # The stub's own announcement, not the recorded PID, is what tells the test the
  # stub is alive and ignoring the signal that the kill sends. The PID is written
  # when the pane starts, which is before the stub is in that state, so waiting
  # on it would let the kill arrive while the trap still halts and would make
  # this a test of the signal branch wearing the orphan branch's name.
  orphan_pid=""
  orphan_waited=0
  while [ ! -e "$orphan_ready" ] && [ "$orphan_waited" -lt 200 ]; do
    sleep 0.1
    orphan_waited=$((orphan_waited + 1))
  done
  if [ -e "$orphan_ready" ]; then
    orphan_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$orphan_pid" ] && kill -0 "$orphan_pid" 2>/dev/null; then
      echo "ok: a phase recorded live pid $orphan_pid and is waiting past the window kill"
    else
      echo "FAIL: the stub announced itself ready but recorded no live PID"; fail=1
    fi
  else
    echo "FAIL: the stub never announced itself ready, so the orphan case could not run"; fail=1
  fi
  tmux kill-window -t "$SESSION:$TMUX_PHASE_WINDOW" 2>/dev/null || true
  # Let the kill be observed before asking for the next tick.
  sleep 0.3
  tmux_session_exists \
    && echo "ok: session outlives a killed phase window (orphan precondition)" \
    || echo "note: session gone; orphan test ran without the persistent-session case"
  drive --self-test || true
  grep -q "orphaned run detected" "$LOG" \
    && echo "ok: orphan detected" || { echo "FAIL: orphan not detected"; fail=1; }
  [ -f "$HALT" ] \
    && echo "ok: orphan halts the line" || { echo "FAIL: orphan did not halt"; fail=1; }
  : >"$orphan_release"
  unset DRIVER_STUB_WAIT_FILE DRIVER_STUB_READY_FILE
  # A pane takes the tmux server's environment, so a stale wait file must leave it too.
  tmux set-environment -gu DRIVER_STUB_WAIT_FILE 2>/dev/null || true; tmux set-environment -gu DRIVER_STUB_READY_FILE 2>/dev/null || true
  unset DRIVER_STUB_COORDINATION
  RECOVER_OWN_CLAIM=0; export RECOVER_OWN_CLAIM
  DRIVER_STUB_SLEEP="$old_stub_sleep"; export DRIVER_STUB_SLEEP
  sleep 0.3
  selftest_kill_own_stub "$(cat "$PIDFILE" 2>/dev/null || true)"
  selftest_kill_own_stub "$orphan_pid"
  rm -f "$orphan_release" "$orphan_ready"

  # A session outlives every phase, so it is created once and reused forever.
  # A session left with a fixed size would otherwise keep it. Assert the
  # reconcile path puts the size options back to `latest`.
  tmux set-option -t "$SESSION" -w window-size manual 2>/dev/null || true
  size_stale="$(tmux show-options -w -t "$SESSION" -v window-size 2>/dev/null || true)"
  tmux_session_ensure
  size_now="$(tmux show-options -w -t "$SESSION" -v window-size 2>/dev/null || true)"
  if [ "$size_now" = "latest" ]; then
    echo "ok: window size option reconciled ${size_stale} -> ${size_now}"
  else
    echo "FAIL: window size option stuck at ${size_now:-gone}, want latest"; fail=1
  fi

  # The status bar must stay high-contrast: the tmux default is dim grey on
  # green, which is unreadable on a display across a room. Also asserts the row
  # count was NOT touched, because changing it would resize the window and
  # reach a running agent.
  case "$(tmux show-option -t "$SESSION" -v status-style 2>/dev/null)" in
    *colour15*) echo "ok: status bar is high contrast" ;;
    *) echo "FAIL: status-style not set, got '$(tmux show-option -t "$SESSION" -v status-style 2>/dev/null)'"; fail=1 ;;
  esac
  [ "$(tmux show-options -t "$SESSION" -v status 2>/dev/null)" = "" ] \
    && echo "ok: status bar row count untouched (no resize of a running phase)" \
    || { echo "FAIL: status row count was changed; that resizes the window"; fail=1; }

  # The stage-title hook is a GATING pre_step hook: a nonzero exit stops the
  # run. These assertions exist to fail loudly if that ever changes.
  local hook_config hook_steps hook_last
  hook_config="$("$PYTHON" - "$PIPELINE" "$REPO" <<'PY'
import sys, yaml
from pathlib import Path
pipeline = yaml.safe_load(Path(sys.argv[1]).read_text())
hook = pipeline.get("hooks", {}).get("pre_step", [])
if isinstance(hook, list) and len(hook) == 2 and hook[0] == "bash":
    path = Path(hook[1])
    print(path if path.is_absolute() else Path(sys.argv[2]) / path)
    for step in pipeline.get("steps", {}):
        print(step)
PY
  )"
  title_hook="$(printf '%s\n' "$hook_config" | head -1)"
  hook_steps="$(printf '%s\n' "$hook_config" | tail -n +2)"
  hook_last="$(printf '%s\n' "$hook_steps" | tail -1)"
  if [ -f "$title_hook" ]; then
    hook_sess="ar-hook-selftest-$$"
    9>&- tmux new-session -d -s "$hook_sess" -x 80 -y 24 -n phase 'sleep 30' 2>/dev/null || true
    hook_rc=0
    while IFS= read -r hook_step; do
      GLIDE_STEP="$hook_step" CLIO_TMUX_SESSION="$hook_sess" \
        bash "$title_hook" >/dev/null 2>&1 || hook_rc=$?
    done <<<"$hook_steps"
    [ "$hook_rc" -eq 0 ] \
      && echo "ok: stage hook exits 0 for configured steps" \
      || { echo "FAIL: stage hook exited $hook_rc"; fail=1; }
    hook_title="$(tmux show-option -t "$hook_sess" -v set-titles-string 2>/dev/null || true)"
    case "$hook_title" in
      *"$hook_last"*) echo "ok: stage hook set the last title ($hook_title)" ;;
      *) echo "FAIL: stage hook title unexpected: '$hook_title'"; fail=1 ;;
    esac
    hook_win="$(tmux list-windows -t "$hook_sess" -F '#{window_name}' 2>/dev/null | tail -1)"
    case "$hook_win" in
      *"$hook_last"*) echo "ok: stage hook renamed the phase window ($hook_win)" ;;
      *) echo "FAIL: stage hook window name unexpected: '$hook_win'"; fail=1 ;;
    esac
    # The rename must not make the window unfindable: the driver looks it up by
    # ID, so a display label like "phase adversary" cannot break a later
    # interrupt, pipe-pane, or geometry read.
    hook_wid="$(tmux list-windows -t "$hook_sess" -F '#{window_id} #{window_name}' 2>/dev/null \
      | awk '$2 ~ /^phase/ { print $1 }')"
    [ -n "$hook_wid" ] \
      && echo "ok: renamed phase window still resolvable by id ($hook_wid)" \
      || { echo "FAIL: renamed phase window is no longer resolvable"; fail=1; }
    # Missing session and missing step must both be silent and harmless.
    GLIDE_STEP="$hook_last" CLIO_TMUX_SESSION="ar-no-such-session-$$" \
      bash "$title_hook" >/dev/null 2>&1 \
      && echo "ok: stage hook tolerates a missing session" \
      || { echo "FAIL: stage hook failed on a missing session"; fail=1; }
    GLIDE_STEP="" CLIO_TMUX_SESSION="$hook_sess" \
      bash "$title_hook" >/dev/null 2>&1 \
      && echo "ok: stage hook tolerates an empty step" \
      || { echo "FAIL: stage hook failed on an empty step"; fail=1; }
    tmux kill-session -t "$hook_sess" 2>/dev/null || true
  elif [ -n "$title_hook" ]; then
    echo "FAIL: stage hook script missing at $title_hook"; fail=1
  else
    echo "ok: no configured stage-title hook"
  fi

}
