#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Choose a phase for this tick and act on the selector's answer.
#
_drive_choose_selection() {
  # The group this tick belongs to is the one this selection carries. It is read
  # here rather than remembered, and it is cleared first so that a stub that
  # answers without one cannot run under a group an earlier call left behind.
  group_take_selection ""
  if [ "${1:-}" = "--self-test" ] && [ -z "${DRIVER_STUB_COORDINATION:-}" ]; then
    out="$(selector_run)" || rc=$?
    if [ "$rc" -ne 0 ]; then
      log "no uncompleted phase (next_phase rc=$rc)"
      { _DRIVE_RC=0; return 0; }
    fi
    number="${out%%$'\t'*}"; rel="${out#*$'\t'}"
    reservation_id=""; reservation_generation=""
    return 0
  fi

  if [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
    _drive_stub_selection
  else
    _drive_real_selection
    [ -z "$_DRIVE_RC" ] || return 0
  fi
    # A selection the board can answer with means an earlier outage is over, so
    # its marker goes and the next outage speaks for itself. This sits after
    # both the stubbed and the real branch and after the merge check, so every
    # path that produces a selection re-arms it: an outage marker only a stub
    # could clear would leave the first real outage silent for ever after. The
    # unavailable state is the one that keeps the marker, which is what makes
    # the notification once per outage rather than once per tick. The first-seen
    # time goes with it, because a stamp beside no marker would be read as the
    # start of an outage that has already ended.
    if [ "$selection_state" != "COORDINATION_UNAVAILABLE" ]; then
      rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
      rm -f "$RUN_DIR/coordination-outage-first-seen"
    fi
    report_needs_decision "$selection"
    case "$selection_state" in
      RESERVED)
        number="$(printf '%s' "$selection" | selection_value phase)"
        rel="$(printf '%s' "$selection" | selection_value path)"
        reservation_id="$(printf '%s' "$selection" | selection_value reservation_id)"
        reservation_generation="$(printf '%s' "$selection" | selection_value generation)"
        group_take_selection "$selection"
        if group_active; then
          # The board claimed the whole group under one reservation id, and this
          # tick runs the member the selection names. Its own roadmap file is
          # what the launch and the branch check need.
          if ! group_resolve_member "$number"; then
            log "group member $number has no roadmap document; refusing to launch"
            { _DRIVE_RC=1; return 0; }
          fi
          rel="$GROUP_MEMBER_REL"
          log "reserved group [$(printf '%s' "$GROUP_MEMBERS" | tr '\n' ' ')] for $PHASE_MACHINE_ID; this tick runs member $number"
        fi
        log "reserved phase $number ($rel) for $PHASE_MACHINE_ID"
        ;;
      WAIT_FOR_CLAIM)
        wait_phase="$(printf '%s' "$selection" | selection_value phase)"
        wait_owner="$(printf '%s' "$selection" | selection_value owner)"
        wait_status="$(printf '%s' "$selection" | selection_value status)"
        # A member of a group this machine already holds. The selector reports
        # the group on this refusal too, so a tick that is continuing a group —
        # or taking one over from a dead owner — knows the array and the branch
        # without having claimed anything itself.
        group_take_selection "$selection"
        if [ "$RECOVER_OWN_CLAIM" = 1 ] \
            && [ "$wait_owner" = "$PHASE_MACHINE_ID" ] \
            && [ "$wait_phase" != "" ]; then
          # Our own claim, left behind by a run that crashed, was stopped, or
          # ended blocked. Take it back explicitly so the phase can finish.
          # A claim held by any other host still stops the line.
          log "recovering own claim on phase $wait_phase (status=$wait_status) for $PHASE_MACHINE_ID"
          local recover_rc=0
          if [ "${DRIVER_STUB_COORDINATION:-}" = "nofence" ]; then
            # A takeover that reports success but hands back no fence.
            printf '{"state": "OK"}\n' >"$RUN_DIR/claim-recovery-$wait_phase.json"
          elif [ -n "${DRIVER_STUB_COORDINATION:-}" ]; then
            printf '{"state": "OK", "reservation": {"reservation_id": "rsv-recovered-stub", "generation": 8, "machine_id": "%s", "phase": "%s", "status": "claimed"}}\n' \
              "$PHASE_MACHINE_ID" "$wait_phase" >"$RUN_DIR/claim-recovery-$wait_phase.json"
          elif ! "$PYTHON" "$ARGUS/board/reservations.py" takeover \
              "${ARGUS_CONFIG_ARGS[@]}" \
              --repo-root "$REPO" --phase "$wait_phase" \
              --machine-id "$PHASE_MACHINE_ID" \
              --expected-reservation-id "$(printf '%s' "$selection" | selection_value reservation_id)" \
              --expected-generation "$(printf '%s' "$selection" | selection_value generation)" \
              --confirm >"$RUN_DIR/claim-recovery-$wait_phase.json" 2>&1; then
            recover_rc=1
          fi
          if [ "$recover_rc" -eq 0 ]; then
            number="$wait_phase"
            # The launch path comes from the work source, because the source is
            # what named the claim and is the only thing that knows where its
            # items live. This used to be a glob of the roadmap folder done here,
            # which put roadmap knowledge back into the driver for no reason: an
            # issue source's recovery would have been searching a roadmap for a
            # file named after an issue. Fail closed rather than fall back to a
            # guess if a result ever arrives without one.
            rel="$(printf '%s' "$selection" | selection_value path)"
            if group_active; then
              # The member this tick launches is the one the board named, and the
              # selection's path is the file of the phase the selector was asked
              # about. They are separate fields, so the member's number is
              # resolved to its own document here rather than launched against
              # the file the selection happened to carry.
              if ! group_resolve_member "$wait_phase"; then
                log "group member $wait_phase has no roadmap document; refusing to launch"
                { _DRIVE_RC=1; return 0; }
              fi
              rel="$GROUP_MEMBER_REL"
              log "recovered group [$(printf '%s' "$GROUP_MEMBERS" | tr '\n' ' ')] on phase $wait_phase for $PHASE_MACHINE_ID"
            fi
            if [ -z "$rel" ]; then
              log "claim recovery for $wait_phase returned no launch path from the work source; refusing to launch"
              { _DRIVE_RC=1; return 0; }
            fi
            # The takeover fences the old generation and issues a new one. The
            # child driver needs that new fence, not the stale one we matched
            # on, so read it back rather than passing the pre-takeover values.
            reservation_id="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reservation",{}).get("reservation_id",""))' \
              "$RUN_DIR/claim-recovery-$wait_phase.json")"
            reservation_generation="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("reservation",{}).get("generation",""))' \
              "$RUN_DIR/claim-recovery-$wait_phase.json")"
            if [ -z "$reservation_id" ] || [ -z "$reservation_generation" ]; then
              log "claim recovery for $wait_phase returned no new fence; refusing to launch (see $RUN_DIR/claim-recovery-$wait_phase.json)"
              { _DRIVE_RC=1; return 0; }
            fi
            log "recovered phase $number ($rel) on $reservation_id generation $reservation_generation; continuing"
          else
            log "claim recovery for phase $wait_phase failed; see $RUN_DIR/claim-recovery-$wait_phase.json"
            { _DRIVE_RC=1; return 0; }
          fi
        else
          log "WAIT_FOR_CLAIM for phase $wait_phase (owner=$wait_owner status=$wait_status); no launch"
          { _DRIVE_RC=0; return 0; }
        fi
        ;;
      COORDINATION_INCONSISTENT)
        log "coordination state conflicts with completion evidence; no launch"
        { _DRIVE_RC=1; return 0; }
        ;;
      COORDINATION_UNAVAILABLE)
        # The board could not be reached or committed to, so the selector could
        # not answer at all. Its whole answer is captured in a shell variable that
        # dies with this tick, the reason is a free-text field inside it, and the
        # selector writes nothing to stderr on this path, so without this the
        # operator had a state name and no reason. The reason is quoted in the
        # log line and the whole answer is kept on disk beside it, which is what
        # the sibling refusals here already do. An absent reason reads without one,
        # the same as it did before the state had a case of its own.
        unavailable_error="$(printf '%s' "$selection" | selection_value error)"
        printf '%s\n' "$selection" >"$RUN_DIR/coordination-selection.json"
        if [ -n "$unavailable_error" ]; then
          unavailable_error="$(cap_length "$(flatten_one_line "$unavailable_error")" 500)"
          log "COORDINATION_UNAVAILABLE ($unavailable_error); refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
        else
          log "COORDINATION_UNAVAILABLE; refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
        fi
        notify_coordination_outage "$unavailable_error"
        { _DRIVE_RC=1; return 0; }
        ;;
      BLOCKED_ON_GATE)
        # A plan that owes the owner a product decision is not runnable. The
        # selector stops here and reserves nothing, so the line waits on a
        # person recording the decision rather than on an agent discovering it
        # mid-run. Exiting 0 stops the driver without failing it: nothing is
        # broken, the queue is waiting on a human.
        gate_phase="$(printf '%s' "$selection" | selection_value phase)"
        gate_error="$(printf '%s' "$selection" | selection_value error)"
        gate_rel="$(printf '%s' "$selection" | selection_value path)"
        log "BLOCKED_ON_GATE for phase $gate_phase ($gate_error); no launch, no reservation"
        log "record the decision in $gate_rel, then re-run the driver"
        { _DRIVE_RC=0; return 0; }
        ;;
      NO_PHASE)
        log "no uncompleted phase"
        { _DRIVE_RC=0; return 0; }
        ;;
      AWAITING_REVIEW)
        # Kept as a case, not as dead weight: a selector that still stops here
        # means next_phase.py and this script disagree about the queue, and the
        # line stopping for that reason is a bug worth naming rather than
        # silently working around. The merge check has already run by the time
        # this is reached, so a phase reported here has genuinely not merged.
        review_phase="$(printf '%s' "$selection" | selection_value phase)"
        review_branch="$(printf '%s' "$selection" | selection_value branch)"
        review_pr="$(printf '%s' "$selection" | selection_value pull_request)"
        log "phase $review_phase is waiting for review on $review_branch, but the selector stopped on it"
        log "the selector should skip an open pull request and continue; this phase now blocks every later one"
        [ -z "$review_pr" ] || log "  pull request: $review_pr"
        [ -z "$STALL" ] || { _DRIVE_RC=0; return 0; }
        notify "Phase $review_phase is waiting for a human, and the selector is holding the whole line behind it.

Branch: $review_branch
Pull request: ${review_pr:-none recorded}"
        { _DRIVE_RC=1; return 0; }
        ;;
      *)
        # A state nobody anticipated is the case where a reason matters most, so
        # the error field is named when the selector sent one. The state name
        # stays in the line either way, so an empty field reads as it did before.
        # The state name goes through the same two helpers as the reason, because
        # the selector's answer is free text in that field too: a newline in it
        # would forge a second entry exactly as one in the reason would.
        unknown_state="$(cap_length "$(flatten_one_line "$selection_state")" 500)"
        unknown_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unknown_error" ]; then
          unknown_error="$(cap_length "$(flatten_one_line "$unknown_error")" 500)"
          log "unexpected coordination result '$unknown_state' ($unknown_error); refusing to launch"
        else
          log "unexpected coordination result '$unknown_state'; refusing to launch"
        fi
        { _DRIVE_RC=1; return 0; }
        ;;
    esac
  return 0
}
