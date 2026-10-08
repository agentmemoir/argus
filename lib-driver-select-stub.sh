#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The self-test's stubbed coordination selection.
#
_drive_stub_selection() {
      # Replay the own-claim recovery path with no board and no real takeover.
      # DRIVER_STUB_COORDINATION is "fence" for a takeover that returns a new
      # fence, or "nofence" for one that returns none. Only the selection and
      # the takeover are stubbed; the case below, the fence handling, and the
      # launch are the real code. This is the path a plain --self-test skips,
      # which is how an unbound reservation_id shipped unnoticed.
      log "self-test: stubbing coordination ($DRIVER_STUB_COORDINATION)"
      local stub_phase=100010
      # A fabricated selection has to look like a real one, and a real one
      # carries the launch path the driver now reads from the result. Its shape
      # is the whole of what matters here: DRIVER_STUB_RC means the launch is
      # stubbed and no file is opened, and a path taken from this machine's real
      # roadmap would make the case depend on which phases it happens to have.
      local stub_dir stub_path
      stub_dir="$(roadmap_dir)"
      stub_path="${stub_dir#"$REPO"/}/phase-$stub_phase-stub-fixture.md"
      selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","path":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7"}' \
        "$stub_phase" "$stub_path" "$PHASE_MACHINE_ID")"
      selection_state=WAIT_FOR_CLAIM
      if [ "${DRIVER_STUB_COORDINATION:-}" = "nopath" ]; then
        # A claim with no launch path in the result. The driver must refuse
        # rather than go looking for the file itself.
        selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7"}' \
          "$stub_phase" "$PHASE_MACHINE_ID")"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "review" ]; then
        # A selection that also carries two phases waiting on a pull request:
        # one merged and one not. The merge check must run on this selection and
        # must not be stopped by the unmerged one, or the merged one is stranded.
        selection="$(printf '{"state":"WAIT_FOR_CLAIM","phase":"%s","path":"%s","owner":"%s","status":"claimed","reservation_id":"rsv-stale-old","generation":"7","awaiting_review":[{"phase":"100010","branch":"feature/review-merged","pull_request":"https://example/pr/1"},{"phase":"100011","branch":"feature/review-open","pull_request":"https://example/pr/2"}]}' \
          "$stub_phase" "$stub_path" "$PHASE_MACHINE_ID")"
        confirm_pending_reviews "$selection"
        [ "$PENDING_PUBLISHED" -eq 0 ] || log "self-test: $PENDING_PUBLISHED phase(s) moved to published; recomputing the selection"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "awaiting" ]; then
        # A selector that still stops on an open pull request. The driver and
        # the selector must not disagree about the queue, so reaching this means
        # the two files are from different versions and the driver halts rather
        # than proceeding on a queue it does not believe.
        selection="$(printf '{"state":"AWAITING_REVIEW","phase":"100010","branch":"feature/review-open","pull_request":"https://example/pr/1","owner":"%s"}' \
          "$PHASE_MACHINE_ID")"
        selection_state=AWAITING_REVIEW
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "unavailable" ]; then
        # A board that could not be reached or committed to, so the selector
        # answers with its own reason instead of a selection. No other stub value
        # can reach this branch, which is why the refusal had no reachable path
        # to be tested on. The reason is multi-line on purpose: the real one is a
        # wrapped git message, and a fix that only coped with a single line would
        # pass here and still lose the reason on the machine that stopped.
        selection="$(printf '{"state":"COORDINATION_UNAVAILABLE","error":"coordination state commit failed: unable to auto-detect email address (got 100010@driver-stub)\\n\\nfatal: unable to auto-detect email address"}')"
        selection_state=COORDINATION_UNAVAILABLE
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "unknown" ]; then
        # A state this case statement has no case for, carrying a reason. Nothing
        # else produces it, and the catch-all is exactly the branch that has to
        # name a reason nobody anticipated.
        selection="$(printf '{"state":"SOME_FUTURE_STATE","error":"the selector reported a state this driver has no case for"}')"
        selection_state=SOME_FUTURE_STATE
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "splitstate" ]; then
        # The same catch-all, with a state name carrying a real line feed. The
        # selector only ever writes fixed names today, so this is latent rather
        # than live, and it is the reason the state name is flattened and capped
        # beside the reason instead of being interpolated as it arrived: an
        # unflattened one would break this line in two, which is the whole defect
        # this code exists to close, reached through the other field of the same
        # answer.
        selection="$(printf '{"state":"SOME_FUTURE\\nSTATE","error":"the selector reported a state this driver has no case for"}')"
        selection_state="$(printf 'SOME_FUTURE\nSTATE')"
      fi
      if [ "${DRIVER_STUB_COORDINATION:-}" = "silent" ]; then
        # The unavailable state with no reason field at all. A selector that
        # refuses without saying why is a real shape, and the two places that
        # answer this state have to read the same way when it happens: one named
        # the state and stopped there, and the other grew an empty pair of
        # brackets because it assumed a reason was always there.
        selection="$(printf '{"state":"COORDINATION_UNAVAILABLE"}')"
        selection_state=COORDINATION_UNAVAILABLE
      fi
  return 0
}
