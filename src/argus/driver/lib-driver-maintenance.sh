#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Settle existing publication receipts without asking the work selector.

pending_review_snapshot() {
  "$PYTHON" - "$ARGUS" "$REPO" "${ARGUS_CONFIG_ARGS[@]}" <<'PY'
import json
import sys

sys.path.insert(0, sys.argv[1])
import _bootstrap
from argus import conventions
from argus.gitsync.receipts import _phase_run_entries
from argus.gitsync.review import awaiting_review

conventions.configure_from_cli(sys.argv[3:])
reviews = []
for number, _ in _phase_run_entries(sys.argv[2]):
    review = awaiting_review(sys.argv[2], number)
    if review:
        reviews.append({"phase": f"{number:06d}", **review})
print(json.dumps({"awaiting_review": reviews}))
PY
}

# Retry every complete group's shared ready, on every tick, whatever state the
# phase's own receipt is in.
#
# The publish step readies the group's one pull request on the tick its last
# member publishes, and the check run that follows that push has almost always
# not finished by then. This is the retry, and it is per tick because the tick is
# the retry: no counter, no timer, and no local ready record. It sweeps every
# receipt rather than the ones waiting on a merge, so a receipt that already
# reads published still gets its pull request readied when its checks finish
# later. The once-only guard is GitHub's own ready/draft timeline, read inside
# the mode, so a pull request a person put back in draft is never re-readied.
#
# It cannot stop the line and never gates a launch: a group that is held or
# still waiting is logged and the next tick asks again.
reconcile_group_ready() {
  local state="" newly=""
  # The self-test has no receipts, board or network.
  [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 0
  mkdir -p "$RUN_DIR"
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
    --mode ready >"$RUN_DIR/group-ready.json" 2>/dev/null || true
  state="$(selection_value state <"$RUN_DIR/group-ready.json" 2>/dev/null || true)"
  case "$state" in
    READY)
      # A pull request that an earlier tick readied is skipped, and its result
      # still reads READY. Log only a result that readied one on this tick.
      newly="$("$PYTHON" -c 'import json, sys
print(any(r.get("state") == "READY" and not r.get("skipped")
          for r in json.load(sys.stdin).get("results", [])))' \
        <"$RUN_DIR/group-ready.json" 2>/dev/null || true)"
      if [ "$newly" = True ]; then
        log "a group's shared pull request is ready; its checks passed"
      fi
      ;;
    HELD)
      log_changed group-ready "group ready: a group pull request is held; the next tick retries"
      ;;
  esac
  return 0
}

reconcile_pending_reviews() {
  PENDING_PUBLISHED=0
  if [ "${WORK_SOURCE:-phases}" = issues ]; then
    # Issue pull requests have no receipt to settle; red CI is still repaired.
    local issue_rows
    [ -n "${REPAIR_PIPELINE:-}" ] && [ -z "${DRIVER_STUB_COORDINATION:-}" ] || return 0
    if issue_rows="$(issue_review_snapshot)"; then
      repair_tick "$issue_rows"
    else
      log "repair: issue pull requests could not be listed; the next tick retries"
    fi
    return 0
  fi
  # Before the receipts are read, and independent of them. A receipt that
  # settles this tick leaves the pending list for ever, so a ready retry placed
  # after this filter would run for one tick and then stop.
  reconcile_group_ready
  local pending
  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    # The self-test has no real receipts, board or network.
    [ "$DRIVER_STUB_COORDINATION" = review ] || return 0
    pending='{"awaiting_review":[{"phase":"100010","branch":"feature/review-merged","pull_request":"https://example/pr/1"},{"phase":"100011","branch":"feature/review-open","pull_request":"https://example/pr/2"}]}'
  elif ! pending="$(pending_review_snapshot)"; then
    # An unreadable receipt is a repair: keep the pending list untouched and
    # retry next tick. It never stops the line.
    log "publication receipts could not be read; keeping them untouched, the next tick retries"
    return 0
  fi
  confirm_pending_reviews "$pending"
  if [ "$PENDING_PUBLISHED" -gt 0 ]; then
    log "$PENDING_PUBLISHED phase(s) moved to published; no new work this tick"
  fi
  # Red CI on a waiting pull request is repaired before any new work.
  repair_tick "$pending"
  # A prior pass may have recorded the merge before its checkout cleanup
  # failed. Published receipts leave the pending list, so retry tidy each pass.
  if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    # A tidy that cannot preserve its work is the narrow lost-work stop; a
    # preserved, degraded, or clean tidy returns 0 and this pass continues.
    tidy_published_checkout || return 1
  fi
  return 0
}
