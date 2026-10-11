#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The readiness report (`--check`).
#
# One line per configured value: the shell variable, its value, and whether a
# flag or the file supplied it. Every schema key has a line, and an optional key
# the file omitted says so rather than reading as a healthy default.
check_config_value() {
  local var="$1" value source
  case "$var" in
    PHASE_INCLUDE|PHASE_EXCLUDE) eval "value=\"\${$var[*]-}\"" ;;
    *) value="${!var-}" ;;
  esac
  value="${value//$'\n'/,}"
  eval "source=\"\${ARGUS_SOURCE_$var-}\""
  if [ -z "$source" ]; then
    printf '%s: %s (unset; not in config)\n' "$var" "${value:-<unset>}"
  else
    printf '%s: %s (%s)\n' "$var" "${value:-<empty>}" "$source"
  fi
}

check() {
  local name label cmd bin selector_rc=0 warned=0 var
  for name in "tmux:$CMD_TMUX" "hermes:$CMD_HERMES" "glide:$CMD_GLIDE"; do
    label="${name%%:*}"; cmd="${name#*:}"
    if bin="$(command -v "$cmd" 2>/dev/null)"; then
      echo "$label: $bin (from '$cmd')"
    elif [ -f "$cmd" ] && [ -x "$cmd" ]; then
      echo "$label: $cmd"
    else
      echo "$label: MISSING (from '$cmd')"; [ "$label" != glide ] || warned=1
    fi
  done
  if "$PYTHON" -c 'import yaml' >/dev/null 2>&1; then
    echo "python3: $PYTHON (PyYAML OK)"
  else
    echo "python3: $PYTHON (PyYAML MISSING - runner.py will fail on import)"
  fi
  if command -v flock >/dev/null 2>&1; then
    echo "flock: $(command -v flock)"
  else
    echo "flock: absent (double-start guard falls back to the tmux session check)"
  fi
  if command -v "$CMD_HERMES" >/dev/null 2>&1 \
      && "$CMD_HERMES" -p "$PROFILE" send --list >/dev/null 2>&1; then
    echo "profile '$PROFILE': usable for send"
  elif [ "${NOTIFY_DISABLED:-0}" = 1 ] || [ -z "$PROFILE" ]; then
    echo "profile: notifications off"
  else
    echo "profile '$PROFILE': NOT usable here (need a local Hermes profile with Discord)"
  fi
  # The configuration report: the file it read, then every schema value with the
  # flag-or-file source the module recorded. This is the one place an operator
  # can see which of the two supplied a value, so it is printed before the
  # folder checks that depend on those values.
  echo "config file: ${ARGUS_CONFIG_FILE:-none}"
  echo "configuration precedence: $CONFIG_PRECEDENCE"
  echo "driver home: $ARGUS_DRIVER"
  for var in REPO PIPELINE ROADMAP_DIR PHASE_NUMBER_INPUT PHASE_FILE_INPUT \
             DRIVER PHASE_INCLUDE PHASE_EXCLUDE WF DRIVER_STATE \
             STALE_AFTER_SEC OUTAGE_STALE_SEC QUIET_MINUTES LOG_KEEP_DAYS \
             MERGE_CHECK_MAX RECOVER_OWN_CLAIM PHASE_MACHINE_ID \
             CMD_GLIDE CMD_GIT CMD_TMUX CMD_PYTHON CMD_HERMES CMD_GH \
             SESSION_NAME TMUX_WIDTH TMUX_HEIGHT TMUX_IDLE_WINDOW \
             TMUX_PHASE_WINDOW TMUX_IDLE_SCRIPT TMUX_REFRESH \
             BOARD_REPO BOARD_FILE BOARD_BRANCH BOARD_REMOTE \
             HERMES_PROFILE_NAME DISCORD_CHANNEL NOTIFY_DISABLED \
             DISCORD_FALLBACK_CHANNEL HEARTBEAT_URL; do
    check_config_value "$var"
  done
  # A folder that does not exist is an error, not a note: the tick it belongs to
  # cannot start, so a report that says nothing about it is a report that reads
  # as healthy.
  for required in "$ARGUS_DRIVER/phase-driver.sh" "$ARGUS/gitsync/main.py" \
                "$PIPELINE"; do
    if [ ! -e "$required" ]; then
      echo "MISSING: $required"
      warned=1
    fi
  done
  echo "work source: ${WORK_SOURCE:-roadmap}"
  if [ "${WORK_SOURCE:-roadmap}" = issues ]; then
    for var in BOARD_ISSUES_FILE MACHINE_SETTINGS_FILE ISSUES_REPO ISSUES_PREFERENCE \
        ISSUES_CATEGORY ISSUES_SEARCH ISSUES_TASK_DIR ISSUES_CODEOWNERS_FILE \
        ISSUES_TRUSTED_AUTHORS ISSUES_TRUSTED_APPS ISSUES_TRUSTED_ASSOCIATIONS \
        ISSUES_RESOLVE_TEAMS ISSUES_UNTRUSTED_COMMENTS ISSUES_MAX_COMMENTS \
        ISSUES_MAX_TEXT_BYTES ISSUES_LIST_LIMIT ISSUES_PR_CHECK_MAX ISSUES_CLAIM_ATTEMPTS \
        ISSUES_ANNOUNCE; do
      check_config_value "$var"
    done
    if ! work_preflight; then
      echo "issue preflight: REFUSED ($PREFLIGHT_REASON)"
      return 1
    fi
    local issue_result issue_rc=0
    issue_result="$(coordination_select --read-only)" || issue_rc=$?
    bind_selection_kind "$issue_result"
    report_issue_selection "$issue_result"
    if [ "$issue_rc" -ne 0 ] && [ "$issue_rc" -ne 1 ] && [ "$issue_rc" -ne 3 ]; then
      echo "issue readiness: REFUSED ($(printf '%s' "$issue_result" | selection_value error))"
      return 1
    fi
    local issue_number issue_path
    issue_number="$(printf '%s' "$issue_result" | selection_value issue)"
    issue_path="$(printf '%s' "$issue_result" | selection_value path)"
    if [ -n "$issue_number" ]; then
      echo "next issue: $issue_number ($issue_path)"
      echo "issue run directory: $(phase_run_dir "$issue_number" "$issue_path")"
    fi
    echo "issue readiness: accepted (exit $issue_rc)"
    return "$warned"
  fi
  # The launch suffix says whether this machine can actually run what it
  # selects: a folder outside this checkout is readable but not launchable.
  if [ -n "$ROADMAP_DIR" ]; then
    if roadmap_dir_readable; then
      echo "roadmap dir: $(roadmap_dir) (configured)"
    else
      echo "roadmap dir: $(roadmap_dir) MISSING OR UNREADABLE, this machine's ticks will refuse to launch"
      warned=1
    fi
    roadmap_dir_is_local \
      || { echo "roadmap dir: WARNING outside this checkout, so its phases cannot be launched here"; warned=1; }
  fi
  # The configured work-source command line, actually run. A source name nothing
  # recognises and a folder that cannot be read both stop the selector with exit
  # 2 and the reason on stderr, and refusing to launch for either is the
  # behaviour this report exists to confirm. Exit 0 (an item to run) and 1
  # (nothing left to run) are both healthy; only the refusal is not.
  #
  # Reporting the refusal is not enough on its own, so a refusal is also the
  # exit code. Printing "work source: no-such-soyrce" and exiting 0 reads as a
  # healthy machine with an odd value, which is how a typo in a settings file
  # reaches a line that then refuses to advance every ten minutes.
  mkdir -p "$RUN_DIR"
  "$PYTHON" "$ARGUS/select/next_phase.py" "${ARGUS_CONFIG_ARGS[@]}" --repo "$REPO" \
    "${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}" >/dev/null 2>"$RUN_DIR/check-selector.err" || selector_rc=$?
  if [ "$selector_rc" -eq 2 ]; then
    echo "work source command line: REFUSED, this machine's ticks will refuse to launch"
    sed 's/^/  /' "$RUN_DIR/check-selector.err" 2>/dev/null || true
    warned=1
  else
    echo "work source command line: accepted (exit $selector_rc)"
  fi
  echo "heartbeat: ${HEARTBEAT_URL:-unset}"
  echo "stall threshold: ${STALE_AFTER_SEC}s"
  echo "tmux geometry: ${TMUX_WIDTH}x${TMUX_HEIGHT} (pinned with window-size manual; a display attached with -r cannot change it)"
  if tmux_session_exists; then
    echo "tmux session '$SESSION': present (persistent, outlives every phase)"
    tmux_window_exists \
      && echo "  phase window: running, $(tmux_phase_window_geometry)" \
      || echo "  phase window: absent (idle)"
    echo "  title: $("$CMD_TMUX" show-option -t "$SESSION" -v set-titles-string 2>/dev/null || echo '(unset)')"
    echo "  attach read-only: tmux attach -t $SESSION -r"
  else
    echo "tmux session '$SESSION': not created yet (created on the next launch)"
  fi
  "$PYTHON" "$ARGUS/select/next_phase.py" --self-test || warned=1
  "$PYTHON" "$ARGUS/board/reservations.py" --self-test || warned=1
  return "$warned"
}
