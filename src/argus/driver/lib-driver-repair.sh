#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-healing: one repair run for a pull request whose checks finished red.
#
# Sourced by phase-driver.sh; not executable on its own. Everything here is off
# unless the configuration has a `repair` object.

# Whether this tick belongs to a repair, so it must not start other work.
#
# Asked by an idle tick before it selects anything, which covers both moments the
# work is due. After a group's last member publishes, the machine is idle and the
# pull request is waiting. Between two members, the next one has not been
# reserved yet, so a repair can still go first. Nothing is claimed on the board
# here, and the answer is judged by the state the Python step printed, never by
# its exit code.
#
# HOLD means the checks on a pull request this machine published have not
# finished. A new member would push before the old head's result is in, and a
# result for a head that is no longer the head is never seen again, so the tick
# waits, for as long as `repair.wait` allows. REPAIR starts one repair run and
# the tick ends. EXHAUSTED, BLOCKED and NONE let the tick go on to its normal
# work, because a pull request that cannot be repaired must not stop the line.
#
# Returns 0 when the tick should end, 1 when it should carry on.
repair_due() {
  [ -n "${REPAIR_PIPELINE:-}" ] || return 1
  [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 1
  local plan="$RUN_DIR/repair-plan.json" state summary
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --mode repair >"$plan" 2>"$plan.err" || true
  state="$(selection_value state <"$plan")"
  summary="$(selection_value summary <"$plan")"
  case "$state" in
    REPAIR)
      log "$summary"
      repair_launch "$(selection_value id <"$plan")" "$(selection_value task <"$plan")" \
        "$(selection_value branch <"$plan")" "$(selection_value base <"$plan")" \
        "$(selection_value nonce <"$plan")" \
        || return 1
      return 0 ;;
    HOLD)
      log_changed repair-hold "$summary"
      return 0 ;;
    EXHAUSTED)
      log_changed repair-exhausted "$summary"
      [ "$(selection_value notify <"$plan")" != True ] || notify "$summary"
      return 1 ;;
    BLOCKED)
      log_changed repair-blocked "$summary"
      return 1 ;;
    NONE)
      return 1 ;;
    *)
      log_changed repair-error "repair: the check gave no answer (see $plan.err)"
      return 1 ;;
  esac
}

# Open the phase window on one repair session.
repair_launch() {
  local id="$1" task="$2" branch="$3" base="$4" nonce="$5" launch quoted setting
  printf -v launch 'bash %q' "$SELF"
  for setting in "${ARGUS_CONFIG_ARGS[@]}"; do
    printf -v quoted ' %q' "$setting"
    launch="$launch$quoted"
  done
  printf -v quoted ' --repair-session %q %q %q %q %q' \
    "$id" "$task" "$branch" "$base" "$nonce"
  launch="$launch$quoted"
  9>&- tmux_session_ensure \
    || { notify "repair $id failed to create tmux session '$SESSION'"; return 1; }
  tmux_set_title "repair $id starting"
  9>&- tmux_phase_window_open "$launch" "$RUN_DIR/session-repair-$id.log" \
    || { notify "repair $id failed to launch in tmux"; return 1; }
  tmux_phase_window_select
  # The session's own clock starts here, so a later tick can name one that
  # outlived its wall time. A round with no leftover from an older session
  # starts clean: both files belong to this launch alone.
  rm -f "$RUN_DIR/repair-$id.start" "$RUN_DIR/repair-$id.stale"
  date +%s >"$RUN_DIR/repair-$id.start"
  log "launched repair $id in tmux '$SESSION' window 'phase'"
}

# Name a repair session that outlived its wall time.
#
# A repair session runs in the phase window, and every tick skips while that
# window is alive, so a check that never ends would stop the line with nothing
# said. This says it: one log line and one notice per session, from whichever
# tick finds the session past `repair.stale-after`. It only reports. A slow
# check and a hung one look the same from here, and killing the session would
# end the attempt on a guess, so nothing is killed.
#
# Returns 0 when a session was reported, 1 when there is nothing to say.
repair_stale() {
  [ "${REPAIR_STALE_AFTER:-0}" -gt 0 ] || return 1
  local start id begun age rc=1 line
  for start in "$RUN_DIR"/repair-*.start; do
    [ -f "$start" ] || continue
    id="$(basename "$start" .start)"
    id="${id#repair-}"
    begun="$(cat "$start" 2>/dev/null || true)"
    # A record that is not a number times no session, and arithmetic on it would
    # end the tick that reads it: skip it.
    case "$begun" in '' | *[!0-9]*) continue ;; esac
    age=$(( $(date +%s) - begun ))
    [ "$age" -ge "$REPAIR_STALE_AFTER" ] || continue
    rc=0
    line="has run ${age}s, at or past its limit of ${REPAIR_STALE_AFTER}s"
    log_changed repair-stale "repair $id $line"
    if [ ! -f "$RUN_DIR/repair-$id.stale" ]; then
      : >"$RUN_DIR/repair-$id.stale"
      notify "Repair $id $line. It may be hung and it never ends; check the phase window."
    fi
  done
  return "$rc"
}

# The changed-scope check of one repair round.
#
# The command is project content: it maps the changed paths to that project's
# tests. The driver only bounds it, keeps its answer, and names a failure. The
# paths go through the environment and never into the command line, because a
# file name is text nothing here controls. No command, no changed path, or a base
# git cannot read prepares nothing, and the next turn then counts the round as it
# always did.
#
# Preparing and running are two steps because the answer must belong to the round
# that started it: the files are written before the push and the answer after it,
# and verify.id carries the round's nonce so a later round cannot read it.

# Write this round's check beside its task, before the push.
repair_verify_prepare() {
  local id="$1" base="$2" nonce="$3" changed folder
  [ -n "${REPAIR_VERIFY:-}" ] || return 0
  if [ -z "$base" ]; then
    log "repair $id: no commit to scope a changed-scope check against; no scope check"
    return 0
  fi
  changed="$("$CMD_GIT" -C "$REPO" diff --name-only "$base" HEAD 2>/dev/null || true)"
  if [ -z "$changed" ]; then
    log "repair $id: the repair changed no path since $base; no scope check"
    return 0
  fi
  folder="$RUN_DIR/repair/$id"
  mkdir -p "$folder"
  printf '%s\n' "$nonce" >"$folder/verify.id"
  printf '%s\n' "$changed" >"$folder/verify.paths"
  printf '%s\n' "$REPAIR_VERIFY" >"$folder/verify.command"
}

# Run the prepared check over the prepared paths, and keep its answer.
#
# The base comes from the caller's ARGUS_REPAIR_BASE, because it is passed on to
# the command and is not one of the three files of the round. A check the
# limiter stopped answers 124, or 137 when it was killed.
repair_verify_run() {
  local id="$1" base="${ARGUS_REPAIR_BASE:-}" folder command changed rc=0 count
  local -a limiter=()
  folder="$RUN_DIR/repair/$id"
  [ -f "$folder/verify.command" ] || return 0
  command="$(cat "$folder/verify.command")"
  changed="$(cat "$folder/verify.paths" 2>/dev/null || true)"
  count="$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"
  if [ "${REPAIR_VERIFY_TIMEOUT:-0}" -gt 0 ] && command -v timeout >/dev/null 2>&1; then
    limiter=(timeout "${REPAIR_VERIFY_TIMEOUT:-0}")
  elif [ "${REPAIR_VERIFY_TIMEOUT:-0}" -gt 0 ] && command -v gtimeout >/dev/null 2>&1; then
    limiter=(gtimeout "${REPAIR_VERIFY_TIMEOUT:-0}")
  elif [ "${REPAIR_VERIFY_TIMEOUT:-0}" -gt 0 ]; then
    log "repair $id: neither timeout nor gtimeout is on PATH; the scope check runs unbounded"
  fi
  "${limiter[@]+"${limiter[@]}"}" env \
    ARGUS_REPAIR_BASE="$base" ARGUS_REPAIR_CHANGED="$changed" \
    bash -c "$command" >"$folder/verify.log" 2>&1 || rc=$?
  printf '%s\n' "$rc" >"$folder/verify.rc"
  if [ "$rc" -eq 0 ]; then
    log "repair $id: the changed scope passes ($count path(s))"
  else
    log "repair $id: the changed scope fails (rc=$rc, $count path(s)); the next task names it"
  fi
}

# Forget this round's check, so the round is counted as it was before the check
# existed: a push that carried nothing, or failed, has no answer to wait for.
repair_verify_clear() {
  local id="$1" folder="$RUN_DIR/repair/$1"
  [ -f "$folder/verify.id" ] || return 0
  rm -f "$folder/verify.id" "$folder/verify.paths" "$folder/verify.command" \
    "$folder/verify.log" "$folder/verify.rc"
  log "repair $id: cleared the prepared scope check; the round is counted as before"
}

# The repair run, inside the phase window.
#
# It never writes the halt marker. A phase that fails stops the line because the
# next phase would build on it; a repair that fails changes nothing the line
# depends on, and the attempt is counted when the next turn reads the round's
# own scope, so the limit ends the retries. The pipeline's agent commits on the
# branch; this pushes whatever it left, without force, and a run that left
# nothing to push counts as a failed attempt.
run_repair_session() {
  local id="$1" task="$2" branch="$3" base="${4:-}" nonce="${5:-}" rc=0 left
  mkdir -p "$RUN_DIR"
  trap 'rm -f "$PIDFILE" "$RUN_DIR/repair-$id.start"; exit 130' HUP INT TERM
  printf '%s\n' "$$" >"$PIDFILE"
  cd "$REPO"
  tmux_set_title "repair $id running"
  "$CMD_GLIDE" run --pipeline "$REPAIR_PIPELINE" \
    --input "repair_id=$id" --input "repair_task=$task" || rc=$?
  if [ "$rc" -eq 0 ]; then
    repair_verify_prepare "$id" "$base" "$nonce"
    if "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
        --mode checkpoint >"$RUN_DIR/repair-$id-push.json" 2>&1; then
      case "$(selection_value action <"$RUN_DIR/repair-$id-push.json")" in
        pushed|committed-and-pushed)
          log "repair $id: pushed to $branch; the next tick reads the new checks"
          tmux_set_title "repair $id checking the changed scope"
          ARGUS_REPAIR_BASE="$base" repair_verify_run "$id"
          tmux_set_title "repair $id done" ;;
        *)
          log "repair $id: the run pushed nothing to $branch"
          repair_verify_clear "$id"
          tmux_set_title "repair $id pushed nothing" ;;
      esac
    else
      log "repair $id: could not push to $branch (see $RUN_DIR/repair-$id-push.json)"
      repair_verify_clear "$id"
      tmux_set_title "repair $id not pushed"
    fi
  else
    log "repair $id: the run ended with rc=$rc and pushed nothing"
    tmux_set_title "repair $id ended (rc=$rc)"
  fi
  left="$("$CMD_GIT" -C "$REPO" status --porcelain 2>/dev/null | head -1 || true)"
  if [ "$rc" -ne 0 ] && [ -n "$left" ]; then
    notify "Repair $id ended with rc=$rc and left uncommitted edits in the checkout on $branch. Read them before the line goes on."
  fi
  rm -f "$PIDFILE" "$RUN_DIR/repair-$id.start"
  exit 0
}
