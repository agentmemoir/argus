#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# The dry-run half of a tick.
#
_drive_dry_run() {
    [ -f "$HALT" ] && { log "dry-run: halted; would refuse to advance"; { _DRIVE_RC=0; return 0; }; }
    tmux_phase_window_alive 2>/dev/null \
      && { log "dry-run: phase window is running; would skip"; { _DRIVE_RC=0; return 0; }; }
    local selection selection_rc=0 state unknown_state
    if ! roadmap_preflight; then
      log "dry-run: $PREFLIGHT_REASON; no launch"
      { _DRIVE_RC=0; return 0; }
    fi
    selection="$(coordination_select --read-only)" || selection_rc=$?
    if [ "$selection_rc" -ne 0 ] && [ "$selection_rc" -ne 1 ] \
        && [ "$selection_rc" -ne 2 ] && [ "$selection_rc" -ne 3 ]; then
      log "dry-run: coordination unavailable; refusing to launch"
      { _DRIVE_RC=0; return 0; }
    fi
    state="$(printf '%s' "$selection" | selection_value state)"
    report_needs_decision "$selection"
    case "$state" in
      AVAILABLE)
        local number rel
        number="$(printf '%s' "$selection" | selection_value phase)"
        rel="$(printf '%s' "$selection" | selection_value path)"
        # A group is claimed as a whole array and run one member per tick, so a
        # dry run that named only the member in front of it would describe a
        # smaller claim than the tick makes. The member's own file is what the
        # launch would use, exactly as in the tick.
        group_take_selection "$selection"
        if group_active && group_resolve_member "$number"; then
          rel="$GROUP_MEMBER_REL"
          log "dry-run: would claim the group [$(printf '%s' "$GROUP_MEMBERS" | tr '\n' ' ')] and launch member $number in tmux '$SESSION'"
        fi
        log "dry-run: would reserve and launch phase $number in tmux '$SESSION'"
        printf 'would launch: tmux new-window -d -t %s -n phase -c %s "bash %s --session %s %s"\n' \
          "$SESSION" "$REPO" "$SELF" "$number" "$rel"
        ;;
      WAIT_FOR_CLAIM)
        wait_phase="$(printf '%s' "$selection" | selection_value phase)"
        wait_owner="$(printf '%s' "$selection" | selection_value owner)"
        if [ "$RECOVER_OWN_CLAIM" = 1 ] \
            && [ "$wait_owner" = "$PHASE_MACHINE_ID" ] \
            && [ "$wait_phase" != "" ]; then
          log "dry-run: would recover this host's own claim on phase $wait_phase and launch it"
        else
          log "dry-run: WAIT_FOR_CLAIM (phase $wait_phase, owner $wait_owner); no launch"
        fi
        ;;
      COORDINATION_INCONSISTENT)
        log "dry-run: coordination state conflicts with completion evidence; no launch"
        ;;
      COORDINATION_UNAVAILABLE)
        # Fixed on its own terms rather than shared with the tick below: this
        # branch dispatches on a different variable and answers 0 because a dry
        # run reports rather than refuses. What it must not do is drop the
        # reason, which is the whole defect. The run directory is made first
        # because the dry-run path returns before the tick creates it, and a
        # missing directory would fail the redirect and leave the log line
        # naming a file that was never written.
        #
        # The answer goes to its own file, not to the one the tick writes. A dry
        # run uses the real run directory unless it has been given another, and an
        # operator who runs one during an outage is asking the same question the
        # tick asked, so writing over the tick's answer would replace the evidence
        # the outage notification points at with a different answer to it.
        mkdir -p "$RUN_DIR"
        printf '%s\n' "$selection" >"$RUN_DIR/coordination-selection.dry-run.json"
        unavailable_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unavailable_error" ]; then
          unavailable_error="$(cap_length "$(flatten_one_line "$unavailable_error")" 500)"
          log "dry-run: COORDINATION_UNAVAILABLE ($unavailable_error); no launch (see $RUN_DIR/coordination-selection.dry-run.json)"
        else
          log "dry-run: COORDINATION_UNAVAILABLE; no launch (see $RUN_DIR/coordination-selection.dry-run.json)"
        fi
        ;;
      BLOCKED_ON_GATE)
        gate_phase="$(printf '%s' "$selection" | selection_value phase)"
        gate_error="$(printf '%s' "$selection" | selection_value error)"
        log "dry-run: BLOCKED_ON_GATE (phase $gate_phase: $gate_error); no launch"
        ;;
      NO_PHASE)
        log "dry-run: no uncompleted phase"
        ;;
      *)
        # A state nobody anticipated is the case where a reason matters most, so
        # the error field is named when the selector sent one. The state name
        # stays in the line either way, so an empty field reads as it did before.
        # The state name goes through the same two helpers as the reason, because
        # the selector's answer is free text in that field too: a newline in it
        # would forge a second entry exactly as one in the reason would.
        unknown_state="$(cap_length "$(flatten_one_line "$state")" 500)"
        unknown_error="$(printf '%s' "$selection" | selection_value error)"
        if [ -n "$unknown_error" ]; then
          unknown_error="$(cap_length "$(flatten_one_line "$unknown_error")" 500)"
          log "dry-run: unexpected coordination result '$unknown_state' ($unknown_error); no launch"
        else
          log "dry-run: unexpected coordination result '$unknown_state'; no launch"
        fi
        ;;
    esac
    { _DRIVE_RC=0; return 0; }
  return 0
}
