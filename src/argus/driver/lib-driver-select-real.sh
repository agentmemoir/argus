#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Acquire a real selection from the board, before it is acted on.
#
_drive_real_selection() {
      # The merge check and the tidy step run before the checkout sync, and the
      # order is the whole of this fix.
      #
      # Merging a pull request deletes its head branch, so the checkout finalize
      # left it on has no upstream left to fetch. sync_one refuses, and the two
      # steps that would have moved the checkout back both ran after that
      # refusal: the merge check needs the selection, and the selection came
      # after the gate. So every merged phase whose branch was deleted stopped
      # the line permanently, and only a human checking out master by hand
      # restarted it. This has now happened twice, which is why it is a fix and
      # not an observation.
      #
      # Both steps before the sync need nothing the sync would have produced.
      # The tidy step reads a receipt and asks for a clean tree and a local
      # integration branch; the merge check asks GitHub about a pull request and
      # reads receipts off disk. The selection needs the private checkout
      # synced, which is what the pre-sync tidy already guarantees, so running it
      # here rather than after the gate costs nothing and unblocks the gate.
      #
      # The gate itself is kept. A checkout that cannot be synced for any other
      # reason — a dirty tree, an unresolvable conflict, a missing remote — still
      # refuses, because nothing here can tell that fault from the one this
      # ordering fixes.
      tidy_published_checkout || { _DRIVE_RC=1; return 0; }
      selection="$(coordination_select)" || selection_rc=$?
      if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 2 ] \
          && [ "$selection_rc" -ne 3 ] \
          && { [ "$selection_rc" -ne 1 ] || [ "${WORK_SOURCE:-roadmap}" != issues ]; }; then
        log "coordination state unavailable; refusing to launch (next_phase rc=$selection_rc)"
        { _DRIVE_RC=1; return 0; }
      fi
      bind_selection_kind "$selection"
      selection_state="$(printf '%s' "$selection" | selection_value state)"
      # A phase whose pull request is merged is a finished phase, and this tick's
      # selection was computed without that fact. Confirm what merged first, so
      # the line never launches a phase the board already considers done.
      #
      # A selection that is already a reservation is never recomputed. This tick
      # holds the phase the selector picked, so asking the selector again can
      # only answer WAIT_FOR_CLAIM for that same phase: it is now the board's
      # lowest claimable one and this host owns it. With recover-own-claim off,
      # the driver reads that as another host holding the phase, refuses the
      # launch, and leaves a claim with no run behind it that every later tick
      # then waits on. The published phases are older than the reserved one, so
      # publishing them does not move which phase is next; it moves the base the
      # phase branches from, and the branch cut at launch resolves that base
      # fresh.
      PENDING_PUBLISHED=0
      if [ -n "$selection_state" ] && [ "$work_kind" != issue ]; then
        confirm_pending_reviews "$selection"
        if [ "$PENDING_PUBLISHED" -gt 0 ]; then
          log "$PENDING_PUBLISHED phase(s) moved to published"
          # A phase the merge check just published may have been the phase this
          # checkout was sitting on, so the tidy step runs rather than being left
          # to the launch path below: the sync gate that follows cannot succeed
          # on a branch whose remote copy is gone. It runs whether or not the
          # selection is recomputed.
          tidy_published_checkout || { _DRIVE_RC=1; return 0; }
          if [ "$selection_state" = "RESERVED" ]; then
            log "keeping this tick's reservation of phase $(printf '%s' "$selection" | selection_value "$item_key")"
          else
            # Nothing is reserved yet, so a recomputation can only move the line
            # to a better phase, and there is no claim to lose.
            log "recomputing the selection"
            selection_rc=0
            selection="$(coordination_select)" || selection_rc=$?
            if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 2 ] \
                && [ "$selection_rc" -ne 3 ]; then
              log "coordination state unavailable after the merge check; refusing to launch"
              { _DRIVE_RC=1; return 0; }
            fi
            selection_state="$(printf '%s' "$selection" | selection_value state)"
          fi
        fi
      fi
      # The gate, after the steps that recover from a dead branch. This is the
      # same fail-closed check as before, on a checkout that is no longer
      # stranded on one.
      #
      # A group names its one branch here once that branch exists, so a checkout
      # that drifted off the branch a half-run group is working on is refused
      # rather than quietly synced on the wrong one. The first tick of a group
      # has no branch yet, and sync_branch_args names none until it does.
      sync_branch_args
      if ! "$PYTHON" "$ARGUS/gitsync/main.py" "${ARGUS_CONFIG_ARGS[@]}" --root "$REPO" --mode start \
          "${SYNC_BRANCH_ARGS[@]+"${SYNC_BRANCH_ARGS[@]}"}" \
          >"$RUN_DIR/coordination-sync.json" 2>/dev/null; then
        log "coordination checkout sync failed; refusing to launch (see $RUN_DIR/coordination-sync.json)"
        { _DRIVE_RC=1; return 0; }
      fi
  return 0
}
