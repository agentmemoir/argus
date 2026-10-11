#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Notifications and the branch/resync state lines.
#
# One line per checkout saying where it actually is, read from the branch
# receipt by the action each checkout reported.
#
# The receipt's top-level "branch" is the name that was asked for, and it is
# the same string for both checkouts. Printing it described a phase branch for
# whichever checkout was really still on master, which is how a swapped pair
# read as a correct launch in the log. Each line below is taken from the
# checkout's own entry instead, and the public one is named by its path rather
# than by position, so the line cannot quietly swap the two.
branch_state_line() {
  "$PYTHON" - "$1" "${BOARD_REPO:-}" <<'PY'
import json, sys
from pathlib import Path
try:
    with open(sys.argv[1]) as fh:
        out = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"branch state unreadable ({exc})")
    raise SystemExit(0)
for repo in out.get("repos", []):
    path = repo.get("repo", "")
    role = "private" if Path(path).resolve() == Path(sys.argv[2]).resolve() else "public"
    where = repo.get("after_branch") or repo.get("action", "?")
    print(f"{role} {where}", end="; ")
PY
}

# One line saying how far the integration branch got into the phase branch.
#
# A conflict is reported with the paths, not as a bare failure, because the
# phase agent is the one who resolves it and a log line naming the files is the
# difference between a phase that finishes and one that stops to ask.
resync_state_line() {
  "$PYTHON" - "$1" "${BOARD_REPO:-}" <<'PY'
import json, sys
from pathlib import Path
try:
    with open(sys.argv[1]) as fh:
        out = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"resync state unreadable ({exc})")
    raise SystemExit(0)
for repo in out.get("repos", []):
    path = repo.get("repo", "")
    role = "private" if Path(path).resolve() == Path(sys.argv[2]).resolve() else "public"
    action = repo.get("action", "?")
    if repo.get("conflicted"):
        print(f"{role} {action} on "
              f"{', '.join(repo['conflicted'])}", end="; ")
    elif repo.get("ok"):
        print(f"{role} {action}", end="; ")
    else:
        blockers = "; ".join(repo.get("blockers") or ["?"])
        print(f"{role} {action} ({blockers})", end="; ")
PY
}

# Best-effort send: bounded by a timeout (when available) and with stdin
# closed, so a hung or interactive hermes can never stall the run.
send_one() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 20 "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout 20 "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  else
    "$CMD_HERMES" -p "$PROFILE" send --to "$1" "$2" </dev/null >>"$LOG" 2>&1
  fi
}

# Notifications are strictly best-effort: a missing profile, a dead Discord or a
# hung hermes must never block or halt the pipeline. On total failure the channel
# is marked dead for this process, so we stop retrying and stop spamming.
notify() {
  local msg="$1" target="discord:$CHANNEL"
  if [ "$NOTIFY_DEAD" = 1 ]; then
    log "notify skipped (channel unavailable): $msg"
    return 0
  fi
  if [ "${NOTIFY_DISABLED:-0}" = 1 ]; then
    log "notify (disabled): $msg"
    return 0
  fi
  if [ -n "${DISABLE_NOTIFY:-}" ] || [ -n "${DISABLE_DISCORD:-}" ]; then
    log "notify (disabled): $msg"
    return 0
  fi
  if [ -z "$PROFILE" ] && [ -z "$CHANNEL" ]; then
    log "notify skipped (not configured): $msg"
    return 0
  fi
  if ! command -v "$CMD_HERMES" >/dev/null 2>&1 && [ ! -x "$CMD_HERMES" ]; then
    log "notify skipped (hermes unavailable: $CMD_HERMES): $msg"
    return 0
  fi
  if send_one "$target" "$msg"; then
    log "notify ok: $msg"; rm -f "$NOTIFY_BROKEN"; return 0
  fi
  sleep 2
  if send_one "$target" "$msg"; then
    log "notify ok (retry): $msg"; rm -f "$NOTIFY_BROKEN"; return 0
  fi
  if [ -n "$FALLBACK" ] && send_one "discord:$FALLBACK" "$msg"; then
    log "notify via fallback channel: $msg"; return 0
  fi
  printf 'notify broken at %s: %s\n' "$(ts)" "$msg" >"$NOTIFY_BROKEN"
  NOTIFY_DEAD=1
  log "notify FAILED (wrote $NOTIFY_BROKEN); further sends skipped this run: $msg"
}

notify_phase() {
  local phase_file="$1" event="$2" reason="${3:-}" exit_code="${4:-}"
  local quiet_minutes="${5:-$QUIET_MINUTES}" message phase_number
  local args=("${ARGUS_CONFIG_ARGS[@]}" --phase-file "$phase_file" --event "$event" --quiet-minutes "$quiet_minutes")
  if [ "${work_kind:-phase}" = issue ]; then args+=(--kind issue); fi
  [ -n "$reason" ] && args+=(--reason "$reason")
  [ -n "$exit_code" ] && args+=(--exit-code "$exit_code")
  message="$("$PYTHON" "$NOTIFIER" "${args[@]}" 2>/dev/null || true)"
  if [ -n "$message" ]; then
    notify "$message"
    return 0
  fi
  phase_number="$("$PYTHON" - "$ARGUS" "$phase_file" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
conventions.configure_from_cli(sys.argv[3:])
from argus.conventions import phase_file_parts
parts = phase_file_parts(sys.argv[2])
print(parts[0] if parts else "unknown")
PY
  )"
  local kind_label=Phase
  [ "${work_kind:-phase}" != issue ] || kind_label=Issue
  notify "$kind_label ${phase_number:-unknown} needs attention."
}

# Say once that the coordination board is unreachable, not once every tick.
#
# The cron entry lives in the Makefile as CRON_LINE, and it is every ten minutes
# across sixteen hours of the day, so an unconditional send would be about
# ninety-six identical messages a day and the operator would learn to ignore the
# channel. That figure is derived from the Makefile rather than read from any
# installed crontab, because no crontab on any machine was checked.
#
# Creating the marker directory is the compare-and-swap: of any number of ticks
# that see the outage together, exactly one creates it and sends. The caller
# removes the marker on the first tick the board answers, which re-arms the next
# outage to speak for itself, and a send that failed removes it here, so a later
# tick tries again rather than staying silent for ever about an outage nobody was
# told of.
#
# A marker left behind by an outage nobody watched end is a different thing, and
# it would silence the channel from then on, because the only thing that clears a
# marker is a tick that reads a usable selection. The marker therefore also
# carries, beside it rather than inside it, the time the outage was first seen,
# and a marker older than the limit below is treated as belonging to an outage
# that is no longer plausibly the current one: it is removed and this tick takes
# its place and speaks. The limit is nine hours because the schedule leaves eight
# hours and ten minutes with no tick at all, so anything shorter would send a
# second message about a single outage that simply ran overnight.
#
# Nothing may ever be written inside the marker directory. It is removed with
# rmdir, which refuses a directory holding anything, so one stray file inside it
# would make every removal fail and every later outage silent with no error
# anywhere. The first-seen time is a separate file for that reason and not a file
# within it.
notify_coordination_outage() {
  local reason="${1-}" marker="$RUN_DIR/coordination-outage"
  local seen="$RUN_DIR/coordination-outage-first-seen"
  local stale_after="$OUTAGE_STALE_SEC" recorded now age
  if [ -d "$marker" ]; then
    recorded="$(cat "$seen" 2>/dev/null || true)"
    case "$recorded" in
      ''|*[!0-9]*) recorded=0 ;;
    esac
    now="$(date +%s 2>/dev/null || echo 0)"
    age=$((now - recorded))
    if [ "$recorded" -gt 0 ] && [ "$age" -gt "$stale_after" ]; then
      rmdir "$marker" 2>/dev/null || true
      rm -f "$seen"
      log "outage marker was $((age / 3600))h old with no sign of recovery; treating it as a new outage"
    fi
  fi
  if mkdir "$marker" 2>/dev/null; then
    date +%s >"$seen" 2>/dev/null || true
    notify "The coordination board cannot be reached, so no ${work_kind:-phase} can start.

The selector said: $reason

Nothing was launched and nothing can be until this is fixed. The selector's whole answer is in $RUN_DIR/coordination-selection.json"
    # notify records a total send failure in its own marker and gives up for the
    # rest of the run, so this is the only place a failed send is visible here.
    if [ "$NOTIFY_DEAD" = 1 ]; then
      rmdir "$marker" 2>/dev/null || true
      rm -f "$seen"
    fi
  fi
  return 0
}
