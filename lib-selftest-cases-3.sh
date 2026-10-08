#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test case: an unavailable board and the file holding its answer.
#
selftest_case_unavailable() {
  # The board answered and said it could not be reached. Everything the refusal
  # owes an operator is asserted here: the reason reaches the log line, the whole
  # answer reaches disk, the launch stays refused, the reason costs one log entry
  # rather than three, a long reason is cut with a count, and an outage speaks
  # once instead of once a tick. The catch-all and the dry-run copy of the same
  # case statement are asserted alongside, because each is its own code.
  #
  # The reason the stub carries is multi-line, because the real one is a wrapped
  # git message. A case that only fitted a single line would pass here and still
  # lose the reason on the machine that stopped, and the log is one entry per
  # line, so a newline that got through would forge a second entry.
  #
  # The halt marker is cleared so a tick does not stop at the halt check before it
  # ever reaches the stub. The one still here was written by the signal-stop case
  # above, which stops the driver and does not take the marker away again. The
  # outage marker is removed because this case counts its notifications and a
  # marker left behind would silence the first one.
  rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  rm -f "$HALT" "$RUN_DIR/coordination-selection.json"
  stub_reason='coordination state commit failed: unable to auto-detect email address (got 100010@driver-stub)\n\nfatal: unable to auto-detect email address'
  # The entry the refusal has to produce, built from the reason the stub carries
  # and the folder the answer is written to, so a change to either half is one
  # failing case and not two.
  unavailable_entry="COORDINATION_UNAVAILABLE ($stub_reason); refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)"
  launches="$(grep -c 'launched phase' "$LOG" || true)"
  unavailable_rc=0
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || unavailable_rc=$?

  if grep -q "unable to auto-detect email address.*coordination-selection\.json" "$LOG"; then
    echo "ok: an unreachable board names its reason and the file holding the whole answer"
  else
    echo "FAIL: the refusal line named neither the selector's reason nor coordination-selection.json"; fail=1
  fi

  # The whole answer, not only the reason. The reason's own newlines have to
  # survive in the file, which is the difference between the file and the log
  # entry: the entry is capped at 500 characters, the file is not.
  selection_probe="$("$PYTHON" - "$RUN_DIR/coordination-selection.json" <<'PROBE' 2>&1 || true
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"unreadable ({exc})")
    raise SystemExit(1)
reason = doc.get("error", "")
carries = "yes" if "unable to auto-detect email address" in reason else "no"
print(f"state={doc.get('state', '')} newlines={reason.count(chr(10))} reason={carries}")
PROBE
)"
  case "$selection_probe" in
    "state=COORDINATION_UNAVAILABLE newlines=2 reason=yes")
      echo "ok: the whole selector answer is on disk, newlines and all" ;;
    *)
      echo "FAIL: coordination-selection.json does not hold the selector's whole answer ($selection_probe)"
      fail=1
      ;;
  esac

  # The launch stays refused, which is the property the case statement above must
  # not weaken: exit 1, no phase window, and no launch logged. The session itself
  # is persistent by design and exists from the case at the top of this function,
  # so its presence proves nothing and the phase window is what is checked.
  #
  # Nothing can be reserved on this path either, and that half is not asserted
  # here: the stub answers the selector, so the reserving code is never entered
  # at all and a case could not tell that from a tick that had reserved and then
  # refused. The strand fixture is the only place in this suite where a claim can
  # be observed, and it drives the real board of a throwaway repository, which a
  # stubbed selection cannot reach.
  if [ "$unavailable_rc" = 1 ] \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$launches" ] \
      && ! tmux_window_exists 2>/dev/null; then
    echo "ok: an unreachable board still refuses the launch, and nothing was opened"
  else
    echo "FAIL: the refusal did not hold (exit $unavailable_rc, launches $(grep -c 'launched phase' "$LOG" || true), window $(tmux_window_exists 2>/dev/null && echo open || echo gone))"
    fail=1
  fi

  # One entry per tick, with the newline shown rather than obeyed. Counting the
  # entries that carry the state is what proves the entry was not broken in two,
  # and comparing the entry with the expected one is what proves the two newlines
  # arrived as two visible characters instead of ending the line and starting
  # another.
  unavailable_entries="$(grep -c 'COORDINATION_UNAVAILABLE (' "$LOG" || true)"
  unavailable_seen="$(grep -m1 'COORDINATION_UNAVAILABLE (' "$LOG" | sed 's/^[^ ]* //' || true)"
  if [ "$unavailable_entries" = 1 ] && [ "$unavailable_seen" = "$unavailable_entry" ]; then
    echo "ok: a multi-line reason is one log entry, with its newlines shown as \\n"
  else
    echo "FAIL: the refusal took $unavailable_entries entries and read '$unavailable_seen'"; fail=1
  fi

  # Once per outage, not once per tick, and once again after the line recovers.
  # The marker directory is the compare-and-swap and the re-arm is what clears
  # it, so this counts the notification the stubbed notifier logged rather than
  # sending anything: DISABLE_NOTIFY and DISABLE_DISCORD are set for the whole of
  # this function, which is how every other case here keeps the channel quiet.
  outage_pattern='notify (disabled): The coordination board cannot be reached'
  # The marker is cleared again first, because the tick above left one and this
  # case has to watch an outage begin rather than continue an earlier one. The
  # first-seen time goes with it, so the outage below is not read as a stale one.
  rmdir "$RUN_DIR/coordination-outage" 2>/dev/null || true
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  outage_before="$(grep -c "$outage_pattern" "$LOG" || true)"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_after_two="$(grep -c "$outage_pattern" "$LOG" || true)"
  # A tick the board can answer means the outage is over, so the marker goes and
  # the next outage speaks for itself. A claim on this host's own phase with no
  # launch path is a tick that answers, and it launches nothing.
  DRIVER_STUB_COORDINATION=nopath drive --self-test >/dev/null 2>&1 || true
  wait_phase_window_gone
  if [ -d "$RUN_DIR/coordination-outage" ]; then outage_marker=kept; else outage_marker=cleared; fi
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_end="$(grep -c "$outage_pattern" "$LOG" || true)"
  # The same outage again, but one nobody watched end. The first-seen time beside
  # the marker is rewritten twice: once recent, so the outage is treated as the
  # current one and stays silent, and once far older than any plausible limit, so
  # it cannot plausibly still be current and the next tick has to speak about it.
  # Without the older case the marker outlives its outage and every outage after
  # it is silent for good; without the recent one, a driver that re-announced on
  # every tick would pass.
  rm -f "$RUN_DIR/coordination-outage-first-seen"
  printf '%s' "$(( $(date +%s) - 60 ))" \
    >"$RUN_DIR/coordination-outage-first-seen"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_recent="$(grep -c "$outage_pattern" "$LOG" || true)"
  printf '%s' "$(( $(date +%s) - 400000 ))" \
    >"$RUN_DIR/coordination-outage-first-seen"
  DRIVER_STUB_COORDINATION=unavailable drive --self-test >/dev/null 2>&1 || true
  outage_stale="$(grep -c "$outage_pattern" "$LOG" || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$outage_after_two" -eq "$((outage_before + 1))" ] \
      && [ "$outage_marker" = cleared ] \
      && [ "$outage_end" -eq "$((outage_before + 2))" ] \
      && [ "$outage_recent" -eq "$((outage_before + 2))" ] \
      && [ "$outage_stale" -eq "$((outage_before + 3))" ]; then
    echo "ok: two ticks of one outage notify once, a tick that answers re-arms, the next outage notifies again, and only a marker old enough to have outlived its outage speaks again"
  else
    echo "FAIL: once-per-outage did not hold (notices $outage_before -> $outage_after_two, marker $outage_marker, ended $outage_end, recent $outage_recent, stale $outage_stale)"
    fail=1
  fi

}
