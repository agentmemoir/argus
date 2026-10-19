#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The work selector, reservation results, and the merge check.
#
# Run the work selector and return its stdout, with its stderr recorded.
#
# stderr is captured because that is where a refusal names its reason and
# nowhere else does: an unknown source or an unreadable phases folder gives
# empty stdout and exit 2, so without this the log would say only that nothing
# was selected and the operator would have to reproduce it by hand.
#
# It is captured and passed through, not moved. The file is the raw reason for a
# human to read; driver.log gets it under a prefix so a line the driver wrote is
# distinguishable from one the selector wrote; and stderr still carries the raw
# text, which is exactly what reached the cron log before this was captured at
# all. Passing it through is therefore additive, and an unconfigured machine's
# log surface is unchanged rather than reshaped.
selector_run() {
  local err="$RUN_DIR/selection.err" out rc=0
  mkdir -p "$RUN_DIR"
  out="$("$PYTHON" "$ARGUS/select/next_phase.py" "${ARGUS_CONFIG_ARGS[@]}" \
    "${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}" "$@" 2>"$err")" || rc=$?
  if [ -s "$err" ]; then
    # Not log() on the driver.log side: on a dry run log() prints to stdout, and
    # this function's stdout is the captured result, so the reason would land
    # inside it. A dry run keeps the raw text on stderr and records nothing,
    # which is what it did before.
    if [ "$DRY_RUN" != 1 ]; then
      # The same warnings on every tick are logged once, as for any other
      # repeat. The raw text still reaches stderr every time.
      if cmp -s "$err" "$RUN_DIR/log-last-selector"; then
        touch "$LOG"
      else
        sed 's/^/selector: /' "$err" >>"$LOG"
        cp "$err" "$RUN_DIR/log-last-selector"
      fi
    fi
    cat "$err" >&2
  else
    rm -f "$RUN_DIR/log-last-selector"
  fi
  printf '%s' "$out"
  return "$rc"
}

coordination_select() {
  # The JSON result is deliberately consumed instead of parsing the legacy
  # tab output. A failed fetch/push must never turn into an unclaimed launch.
  selector_run --server --machine-id "$PHASE_MACHINE_ID" --json "$@"
}

# One field out of the selector's JSON result, or nothing when there is no result.
#
# "Or nothing" is the case that matters. Every refusal comes back as an empty
# result with a non-zero exit, because an unreadable folder and a queue that has
# run dry are indistinguishable from the exit code alone. Parsing that empty
# result therefore has to answer with nothing rather than a traceback: a
# traceback is Python's error, printed where the operator is reading, on the
# exact path whose job is to name what went wrong. The caller already treats an
# empty field as "no value" and refuses the launch.
selection_value() {
  local key="$1"
  "$PYTHON" -c 'import json, sys
try:
    print(json.load(sys.stdin).get(sys.argv[1], ""))
except ValueError:
    pass
' "$key"
}

# One line per phase the selector skipped because its pull request is open.
#
# The selector no longer stops on an open pull request, so nothing in selection
# runs the merge check, and a receipt would read awaiting_review for ever: the
# phase that was merged never gets recorded as merged and the board keeps no
# completion evidence for it. The tick's maintenance pass runs this before any
# selection is acted on.
#
# Newest first, deliberately. The phase that blocks the line is the highest
# numbered one still waiting, because the next phase is stacked on the highest
# open predecessor. Checking in phase order spends the cap on old receipts and
# can leave the blocking phase unexamined on every tick, which is the stall
# this whole path exists to end.
pending_review_lines() {
  "$PYTHON" -c '
import json, sys
entries = json.load(sys.stdin).get("awaiting_review", []) or []
for entry in sorted(entries, key=lambda e: str(e.get("phase") or ""), reverse=True):
    print("\t".join([str(entry.get("phase") or ""),
                     str(entry.get("branch") or ""),
                     str(entry.get("pull_request") or "")]))
'
}

# Say which phases the selector passed over because their run ended owing a
# person a decision: a rejected run, or a remedy loop that ran out of rounds.
# The selector skips them so the line can advance; this is what stops the skip
# being silent. Reported on every selection, including one that launched
# nothing, so a queue waiting on a decision reads as waiting rather than empty.
report_needs_decision() {
  local selection="$1" phase state path
  while IFS=$'\t' read -r phase state path; do
    [ -n "$phase" ] || continue
    log "phase $phase ended $state; skipped until a person decides ($path)"
  done < <(printf '%s' "$selection" | "$PYTHON" -c '
import json, sys
entries = json.load(sys.stdin).get("needs_decision", []) or []
for entry in entries:
    print("\t".join([str(entry.get("phase") or ""),
                     str(entry.get("state") or ""),
                     str(entry.get("path") or "")]))
' 2>/dev/null)
}

# Return a checkout sitting on a published phase branch to the integration
# branch, before anything that would refuse on the dead branch.
#
# The same `--mode tidy` the launch path runs, called here as well as there. It
# is idempotent and needs no network: it reads a receipt off disk and asks git
# for a clean tree and a local integration branch. Running it before the
# checkout sync is what makes a merged-then-deleted phase branch recoverable —
# see the ordering note in _drive().
#
# A refusal is a refusal, with one deferral. The phase on this branch is not
# published yet, the tree is dirty, or the integration branch is missing: each
# of those is tidy's own fault to report, and the tick stops. But a root that
# is not a git working tree is not tidy's business at all — the sync gate two
# lines below reports it with its own message, and stopping here would replace
# that message with one naming the wrong step. So that one result is deferred
# and the gate answers it.
tidy_published_checkout() {
  local rc=0 not_a_tree=0
  "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode tidy \
    "${GITSYNC_KIND_ARGS[@]+"${GITSYNC_KIND_ARGS[@]}"}" \
    >"$RUN_DIR/phase-tidy.json" 2>/dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  repaired="$("$PYTHON" - "$RUN_DIR/phase-tidy.json" <<'PROBE' 2>/dev/null || printf 'no'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError):
    print("no")
    raise SystemExit(0)
repos = doc.get("repos") or []
# A lost checkout is a repair, not a stop: every checkout that did not move
# said so by its own warning. A blocked result (a dirty tree that would lose
# work) is the narrow G9 stop below.
def lost(r):
    return any("not a git working tree" in b for b in (r.get("blockers") or [])) \
        or any("not a git working tree" in w for w in (r.get("warning") or [],))
repaired_result = repos and all(lost(r) for r in repos)
print("yes" if repaired_result else "no")
PROBE
)"
  if [ "$repaired" = "yes" ]; then
    log "could not return a published phase's checkout to the integration branch; the next tick retries (see $RUN_DIR/phase-tidy.json)"
    return 0
  fi
  log "could not return a published phase's checkout to the integration branch; refusing to launch (see $RUN_DIR/phase-tidy.json)"
  return 1
}

# Run the merge check for every phase waiting on a pull request. Sets
# PENDING_PUBLISHED to the number that became published, so the caller knows a
# receipt settled on this tick and ends the tick before selecting.
#
# A global rather than an echoed count, because log writes to stdout in a dry
# run and to the log file otherwise, so anything captured from this function's
# stdout would carry log lines on one path and not the other.
#
# The exit code alone is deliberately not what is read. `--mode published` exits
# non-zero both when the merge has not happened and when the merge HAS happened
# but the checkout could not be moved afterwards; treating the second as the
# first reported a finished phase as still waiting. The result JSON says which,
# so that is what is read.

confirm_pending_reviews() {
  local phase branch pr rc=0 state checked=0 cap
  PENDING_PUBLISHED=0
  MERGE_CHECK_HEALTH="ok"
  # Bounded, because each phase costs a gh call and possibly a fetch, and a
  # selection is not a place where unbounded network work belongs. Five keeps
  # the worst case (five calls at the 15s bound in gitsync.py) to about 75
  # seconds, inside the cron interval. Any stack deeper than that is checked on
  # a later tick, and hitting the cap says so.
  cap="$MERGE_CHECK_MAX"
  while IFS="$(printf '\t')" read -r phase branch pr; do
    [ -n "$phase" ] || continue
    if [ "$checked" -ge "$cap" ]; then
      log "merge check: stopped after $cap phase(s); the rest are checked on a later tick"
      MERGE_CHECK_HEALTH="capped at $cap"
      break
    fi
    if [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
      "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" \
        --mode published --phase "$phase" \
        >"$RUN_DIR/phase-published-$phase.json" 2>/dev/null || rc=$?
    elif [ "${DRIVER_STUB_MERGED_PHASE:-}" = "$phase" ]; then
      # The stub keeps the self-test off the network and off real repositories.
      rc=0
      printf '{"state": "PUBLISHED"}\n' >"$RUN_DIR/phase-published-$phase.json"
    elif [ "${DRIVER_STUB_COORDINATION:-}" = "review" ]; then
      rc=1
      printf '{"state": "BLOCKED", "repos": [{"ok": false, "github": {"state": "unknown", "detail": "gh exited 4"}}]}\n' \
        >"$RUN_DIR/phase-published-$phase.json"
    else
      rc=1
    fi
    checked=$((checked + 1))
    state="$(printf '%s' "$(selection_value state <"$RUN_DIR/phase-published-$phase.json")" 2>/dev/null || true)"
    case "$state" in
      PUBLISHED)
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        log "phase $phase is merged; marked published and returned the public checkout to the integration branch"
        ;;
      PUBLISHED-TIDY-PENDING)
        # The merge is real and recorded; only the branch switch failed. The
        # tidy step below finishes it, so this is progress, not a wait.
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        log "phase $phase is merged and recorded, but the checkout could not be returned to the integration branch yet; the tidy step will retry it"
        ;;
      PUBLISHED-GROUP-PENDING)
        # One branch and one pull request cover a whole group, so this member is
        # merged while its group's other members are not. The checkout stays on
        # the group branch, because the members still to run need it. Progress,
        # like the two above, and the last member of the group is the one that
        # returns the checkout to the integration branch.
        PENDING_PUBLISHED=$((PENDING_PUBLISHED + 1))
        log "phase $phase is merged and recorded; the checkout stays on the group branch until every member of the group is published"
        ;;
      *)
        if grep -q '"state": *"unknown"' "$RUN_DIR/phase-published-$phase.json" 2>/dev/null; then
          MERGE_CHECK_HEALTH="UNAVAILABLE (gh could not be asked)"
        fi
        # One line per phase, logged when it changes: a pull request can wait
        # days, and the same three lines every ten minutes buried everything else.
        log_changed "waits-$phase" "phase $phase still waits: $(sed -n 's/.*"blockers": \[\(.*\)\].*/\1/p' "$RUN_DIR/phase-published-$phase.json" 2>/dev/null | head -1) (branch: $branch${pr:+; pull request: $pr})"
        if [ "$MERGE_CHECK_HEALTH" = "UNAVAILABLE (gh could not be asked)" ]; then
          log_changed degraded "  the squash-merge check is DEGRADED: a squash-merged phase will keep waiting until gh works again"
        fi
        ;;
    esac
    rc=0
  done < <(pending_review_lines <<<"$1")
  log_changed merge-check "merge check: $MERGE_CHECK_HEALTH ($checked phase(s) examined)"
  # A degraded merge check is announced, not just logged. This is the condition
  # that hid the stall: gh could not be asked, every phase stayed "awaiting
  # review", the next phase was cut from a merged branch, and the branch guard
  # refused it on every tick with nothing but a log line to say so. The operator
  # has to hear it from the line, because the symptom they see is a phase that
  # never starts and a reason that does not explain it.
  if [ "$MERGE_CHECK_HEALTH" = "UNAVAILABLE (gh could not be asked)" ]; then
    notify "The phase line cannot confirm merges right now.

GitHub could not be asked, so a pull request a human already squash-merged may still read as waiting. A phase whose branch was deleted after merging cannot be proved merged from local git alone, and the line will refuse to start the next phase until gh works.

Fix: authenticate gh on this host, then let the driver rerun."
  fi
}
