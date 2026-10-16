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

reconcile_pending_reviews() {
  PENDING_PUBLISHED=0
  PENDING_READY=0
  [ "${WORK_SOURCE:-phases}" != issues ] || return 0
  local pending
  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    # The self-test has no real receipts, board or network.
    [ "$DRIVER_STUB_COORDINATION" = review ] || return 0
    pending='{"awaiting_review":[{"phase":"100010","branch":"feature/review-merged","pull_request":"https://example/pr/1"},{"phase":"100011","branch":"feature/review-open","pull_request":"https://example/pr/2"}]}'
  elif ! pending="$(pending_review_snapshot)"; then
    log "publication receipts could not be read; refusing to advance"
    return 1
  fi
  confirm_pending_reviews "$pending"
  if [ "$PENDING_PUBLISHED" -gt 0 ]; then
    log "$PENDING_PUBLISHED phase(s) moved to published; no new work this tick"
  fi
  # A prior pass may have recorded the merge before its checkout cleanup
  # failed. Published receipts leave the pending list, so retry tidy each pass.
  if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    tidy_published_checkout || return 1
  fi
  return 0
}
