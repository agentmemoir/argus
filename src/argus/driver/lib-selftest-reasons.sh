#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Self-test cases: unexpected states, the dry-run copy, and the reason cap.
#
selftest_case_reasons() {
  # A state this case statement has no case for, carrying a reason. The catch-all
  # is where a reason matters most, because it exists for a state nobody
  # anticipated, and it must still refuse.
  selftest_replay_log
  unknown_launches="$(grep -c 'launched phase' "$LOG" || true)"
  unknown_rc=0
  DRIVER_STUB_COORDINATION=unknown drive --self-test >/dev/null 2>&1 || unknown_rc=$?
  if grep -q "unexpected coordination result 'SOME_FUTURE_STATE' (the selector reported a state this driver has no case for)" "$LOG" \
      && [ "$unknown_rc" = 1 ] \
      && [ "$(grep -c 'launched phase' "$LOG" || true)" = "$unknown_launches" ]; then
    echo "ok: an unexpected state names its reason and still refuses"
  else
    echo "FAIL: an unexpected state did not name its reason, or did not refuse (exit $unknown_rc)"; fail=1
  fi

  # The same catch-all, with the state name carrying a line feed instead of a
  # reason that does. The state name is free text in the selector's answer just as
  # the reason is, and it reaches the same log line, so it is flattened and capped
  # the same way. The comparison is by whole entry rather than by a substring,
  # because a substring would also match a log that had been broken in two and had
  # the rest of the phrase somewhere else in the file.
  split_rc=0
  DRIVER_STUB_COORDINATION=splitstate drive --self-test >/dev/null 2>&1 || split_rc=$?
  split_seen="$(grep -c "unexpected coordination result 'SOME_FUTURE\\\\nSTATE' (the selector reported a state this driver has no case for); refusing to launch" "$LOG" || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$split_seen" = 1 ] && [ "$split_rc" = 1 ]; then
    echo "ok: a state name carrying a line feed is shown as \\n and stays one log entry"
  else
    echo "FAIL: a state name carrying a line feed reached the log line as it arrived (exit $split_rc, matching lines $split_seen)"
    fail=1
  fi

  # The unavailable state with no reason at all. The line has to read the way it
  # read before the state had a case of its own, with no empty brackets in it,
  # because the whole of the change here is adding a reason and a state that has
  # none is the case where adding one must not show.
  silent_rc=0
  DRIVER_STUB_COORDINATION=silent drive --self-test >/dev/null 2>&1 || silent_rc=$?
  silent_line="$(grep -m1 'COORDINATION_UNAVAILABLE; refusing to launch' "$LOG" | sed 's/^[^ ]* //' || true)"
  unset DRIVER_STUB_COORDINATION
  if [ "$silent_rc" = 1 ] \
      && [ "$silent_line" = "COORDINATION_UNAVAILABLE; refusing to launch, nothing reserved (see $RUN_DIR/coordination-selection.json)" ]; then
    echo "ok: an unavailable board that gave no reason is named without empty brackets"
  else
    echo "FAIL: an unavailable board with no reason did not read cleanly (exit $silent_rc, line '$silent_line')"
    fail=1
  fi

  # The dry-run half of the same case statement. It dispatches on a different
  # variable and answers 0 because a dry run reports rather than refuses, so it
  # is its own code and needs its own case: an operator who runs a dry run to find
  # out why nothing is starting is asking the same question. DRY_RUN is set here
  # because that is all the --dry-run entry point does.
  #
  # The answer comes from the selector itself rather than from the stub, because
  # the stub is read on the tick path only. A private coordination folder that is
  # not a git checkout is the selector's own way of saying the board cannot be
  # reached, and the store refuses on it before it fetches anything, so no board
  # is contacted.
  #
  # The selector's flag array is saved and restored by a copy that expands it in
  # a form every bash accepts, so a host with no work source configured can run
  # the selector at all. Expanding an empty array plainly is an unbound-variable
  # error under `set -u` on bash older than 4.4, which is every Mac, and that
  # aborted the whole run here.
  #
  # A dry run prints to stdout, so its output is kept in a file rather than
  # captured in a substitution. That is no longer a workaround: drive releases
  # the portable lock before it returns, so calling it inside a substitution no
  # longer leaves the lock held for the rest of the process. The file is kept
  # because a dry run's stdout is its result, and this case reads the result
  # twice.
  old_work_source_flags=("${WORK_SOURCE_FLAGS[@]+"${WORK_SOURCE_FLAGS[@]}"}")
  WORK_SOURCE_FLAGS=(--source phases)
  load_board_override "$tmp/unavailable-board-checkout"
  # The tick's own answer is planted first, so the case can tell a dry run that
  # left it alone from one that wrote over it. It is the file an outage
  # notification points the operator at, and a dry run answers a different
  # question with a different reason, so replacing it would replace the evidence.
  printf '%s\n' '{"state":"TICK_ANSWER_PLANTED"}' >"$RUN_DIR/coordination-selection.json"
  rm -f "$RUN_DIR/coordination-selection.dry-run.json"
  DRY_RUN=1
  drive >"$RUN_DIR/dry-unavailable.out" 2>/dev/null || true
  DRY_RUN=0
  load_fixture_config "$selftest_config"
  dry_out="$(cat "$RUN_DIR/dry-unavailable.out" 2>/dev/null || true)"
  if printf '%s' "$dry_out" | grep -q 'dry-run: COORDINATION_UNAVAILABLE (configured board checkout is unavailable: '; then
    echo "ok: a dry run names the reason the board could not be reached"
  else
    echo "FAIL: a dry run did not name the reason (last line: $(printf '%s' "$dry_out" | tail -1))"; fail=1
  fi
  if [ -f "$RUN_DIR/coordination-selection.dry-run.json" ] \
      && printf '%s' "$dry_out" | grep -qF "coordination-selection.dry-run.json"; then
    echo "ok: a dry run keeps its whole answer on disk under a name of its own, and says so"
  else
    echo "FAIL: a dry run named no file of its own, or named one it never wrote"; fail=1
  fi
  if grep -q 'TICK_ANSWER_PLANTED' "$RUN_DIR/coordination-selection.json"; then
    echo "ok: a dry run does not overwrite the answer the outage notification points at"
  else
    echo "FAIL: a dry run overwrote the tick's own coordination-selection.json"; fail=1
  fi

  # A reason longer than the cap is cut, and the cut says how much was dropped,
  # because a bare cut reads as a whole reason when it is half of one. The
  # expected entry is built from the answer the selector really gave, read back
  # out of the file it wrote, so the case pins the cap at 500 and the dropped
  # count without pinning anything about this machine's temporary folder. The
  # path is long rather than the reason, because a path is what a machine can
  # make long by accident, and it carries no character a log entry has to flatten.
  # It is built from several folders rather than one long name because a single
  # name has a length limit of its own, and a name past it is answered with an
  # error the selector does not catch, so the case would measure a crash instead
  # of a refusal.
  long_private="$tmp"
  long_segment=0
  while [ "$long_segment" -lt 5 ]; do
    long_private="$long_private/$(printf 'selftest-long-reason-segment-%090d' "$long_segment")"
    long_segment=$((long_segment + 1))
  done
  load_board_override "$long_private"
  rm -f "$RUN_DIR/coordination-selection.dry-run.json"
  DRY_RUN=1
  drive >"$RUN_DIR/dry-long-reason.out" 2>/dev/null || true
  DRY_RUN=0
  load_fixture_config "$selftest_config"
  long_out="$(cat "$RUN_DIR/dry-long-reason.out" 2>/dev/null || true)"
  full_reason=""
  if [ -f "$RUN_DIR/coordination-selection.dry-run.json" ]; then
    full_reason="$(selection_value error <"$RUN_DIR/coordination-selection.dry-run.json" 2>/dev/null || true)"
  fi
  if [ "${#full_reason}" -gt 500 ]; then
    long_entry="dry-run: COORDINATION_UNAVAILABLE (${full_reason:0:500}...[+$(( ${#full_reason} - 500 )) bytes truncated]); no launch"
    if printf '%s' "$long_out" | grep -qF "$long_entry"; then
      echo "ok: a reason over 500 bytes is cut at 500 and says how many were dropped"
    else
      echo "FAIL: a reason of ${#full_reason} bytes was not cut at 500 with the dropped count (last line: $(printf '%s' "$long_out" | tail -1 | cut -c1-90))"
      fail=1
    fi
  else
    echo "FAIL: the long-reason case never produced a reason over 500 bytes (got ${#full_reason}; last line: $(printf '%s' "$long_out" | tail -1 | cut -c1-90))"
    fail=1
  fi

  # Two more runs of characters, placed so that the byte the cap cuts at falls
  # inside them rather than between them. The case above cuts a run of ASCII at a
  # boundary, so it cannot tell a cut that repairs itself from one that splits a
  # character, and a reason that arrives from git can carry an accent, a branch
  # name written in another script, or a line feed.
  #
  # A path is the only free text that reaches a real selector's reason here, and
  # the reason begins with a fixed sentence followed by a path the selector
  # resolves, so where byte 500 lands depends on this machine's temporary folder
  # rather than on anything the driver chose. It is therefore measured from the
  # same resolution the selector performs, and the expected entry is computed by a
  # separate program reading the answer the selector really wrote, so the case
  # cannot agree with a wrong cut by using the code under test to check itself.
  cut_base="$("$PYTHON" - "$tmp/cut-probe" <<'PY' 2>/dev/null || echo 0
import pathlib, sys
# The sentence the store puts in front of the path it could not use, and the
# resolved path plus the slash that starts the first folder name.
sentence = "configured board checkout is unavailable: "
print(len(sentence) + len(str(pathlib.Path(sys.argv[1]).resolve())) + 1)
PY
)"
  cut_entry_ok=yes
  cut_multibyte_ok=no
  cut_escape_ok=no
  cut_multibyte_detail=none
  cut_escape_detail=none
  cut_probe=none
  for cut_width in 3 2; do
    # Width 3 is a character of three bytes and width 2 is a line feed, which the
    # flatten above turns into a backslash and an n. Both are the same question,
    # so both are asked the same way: shift the run by as many ASCII characters as
    # it takes for the cut to land where this shape needs it, rather than hoping
    # a folder name of the wrong length did it.
    if [ "$cut_width" = 3 ]; then
      cut_char='世'
      cut_per=80
    else
      cut_char='
'
      cut_per=120
    fi
    # Every folder carries 240 bytes of the reason once flattened, and a folder
    # name has a length limit of its own, which is why the run is split across
    # folders rather than put in one long name.
    cut_stride=241
    cut_seg=$(( (499 - cut_base) / cut_stride ))
    [ "$cut_seg" -gt 0 ] || cut_seg=0
    cut_in=$(( 499 - cut_base - cut_seg * cut_stride ))
    while [ "$cut_in" -lt "$cut_width" ] && [ "$cut_seg" -gt 0 ]; do
      cut_seg=$(( cut_seg - 1 ))
      cut_in=$(( cut_in + cut_stride ))
    done
    cut_pad=$(( cut_in % cut_width ))
    cut_pad_name=""
    cut_index=0
    while [ "$cut_index" -lt "$cut_pad" ]; do
      cut_pad_name="p$cut_pad_name"
      cut_index=$(( cut_index + 1 ))
    done
    cut_total=1
    while [ "$(( cut_base + cut_total * cut_stride ))" -lt 503 ]; do
      cut_total=$(( cut_total + 1 ))
    done
    [ "$cut_total" -gt "$(( cut_seg + 1 ))" ] || cut_total=$(( cut_seg + 1 ))
    cut_private="$tmp/cut-probe"
    cut_name=""
    cut_index=0
    while [ "$cut_index" -lt "$cut_per" ]; do
      cut_name="$cut_name$cut_char"
      cut_index=$(( cut_index + 1 ))
    done
    cut_index=0
    while [ "$cut_index" -lt "$cut_total" ]; do
      if [ "$cut_index" = "$cut_seg" ]; then
        cut_private="$cut_private/$cut_pad_name$cut_name"
      else
        cut_private="$cut_private/$cut_name"
      fi
      cut_index=$(( cut_index + 1 ))
    done
    # One ordinary folder after the run, because the selector trims the setting it
    # reads and a last folder made only of line feeds would be trimmed off the end
    # of the answer before the case ever saw it.
    cut_private="$cut_private/cut-run-end"
    load_board_override "$cut_private"
    rm -f "$RUN_DIR/coordination-selection.dry-run.json"
    DRY_RUN=1
    drive >"$RUN_DIR/dry-cut-reason.out" 2>/dev/null || true
    DRY_RUN=0
    load_fixture_config "$selftest_config"
    cut_probe="$("$PYTHON" - "$RUN_DIR/coordination-selection.dry-run.json" \
        "$RUN_DIR/coordination-selection.dry-run.json" <<'PY' 2>&1 || true
import json, sys
try:
    with open(sys.argv[1]) as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print(f"unreadable ({exc})")
    raise SystemExit(1)
reason = doc.get("error", "")
flat = reason.replace("\r", "").replace("\n", "\\n")
raw = flat.encode("utf-8")
cut = raw[:500]
while cut:
    last = cut[-1]
    if 0x80 <= last <= 0xBF or last >= 0xC0:
        if last < 0xC0:
            cut = cut[:-1]
            continue
        cut = cut[:-1]
        break
    break
try:
    kept = cut.decode("utf-8")
    whole = "yes"
except UnicodeDecodeError:
    kept = cut.decode("utf-8", "replace")
    whole = "no"
edge = raw[499] if len(raw) > 499 else -1
print("line=%s" % (
    "dry-run: COORDINATION_UNAVAILABLE (%s...[+%d bytes truncated]); no launch (see %s)"
    % (kept, len(raw) - len(cut), sys.argv[2])))
print("whole=%s" % whole)
print("edge=%s" % ("lead" if edge >= 0xC0 else
                  "backslash" if raw[499:500] == b"\\" else
                  "continuation" if edge >= 0x80 else "ascii"))
PY
)"
    cut_line="$(printf '%s\n' "$cut_probe" | sed -n 's/^line=//p' || true)"
    cut_whole="$(printf '%s\n' "$cut_probe" | sed -n 's/^whole=//p' || true)"
    cut_edge="$(printf '%s\n' "$cut_probe" | sed -n 's/^edge=//p' || true)"
    if ! printf '%s' "$(cat "$RUN_DIR/dry-cut-reason.out" 2>/dev/null || true)" | grep -qF "$cut_line"; then
      cut_entry_ok=no
    fi
    # One verdict per shape, and each shape's own test decides only its own: a
    # shared else would let the second shape's answer overwrite the first's.
    case "$cut_width" in
      3)
        if [ "$cut_whole" = yes ] && [ "$cut_edge" = lead ]; then
          cut_multibyte_ok=yes
        fi
        cut_multibyte_detail="edge ${cut_edge:-none}, whole ${cut_whole:-none}"
        ;;
      2)
        if [ "$cut_edge" = backslash ]; then
          cut_escape_ok=yes
        fi
        cut_escape_detail="edge ${cut_edge:-none}"
        ;;
    esac
  done

  load_fixture_config "$selftest_config"
  if [ "$cut_multibyte_ok" = yes ] && [ "$cut_escape_ok" = yes ] \
      && [ "$cut_entry_ok" = yes ]; then
    echo "ok: a cut inside a multi-byte character keeps the whole character and the line still decodes, and a cut between the two characters of a flattened newline is pinned"
  else
    echo "FAIL: a cut inside a character was not repaired (multibyte $cut_multibyte_ok: $cut_multibyte_detail; escape $cut_escape_ok: $cut_escape_detail; entry $cut_entry_ok)"
    echo "      probe: $(printf '%s' "$cut_probe" | tr '\n' '|' | cut -c1-200)"
    echo "      line the driver wrote: $(tail -c 200 "$RUN_DIR/dry-cut-reason.out" 2>/dev/null | tr '\n' '|')"
    fail=1
  fi
  WORK_SOURCE_FLAGS=("${old_work_source_flags[@]+"${old_work_source_flags[@]}"}")

}
