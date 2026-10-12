#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Timestamps, logging, and the log-line sanitising helpers.
#
ts() { date +%Y-%m-%dT%H:%M:%S%z; }

log() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '%s %s\n' "$(ts)" "$*"
  else
    printf '%s %s\n' "$(ts)" "$*" >>"$LOG"
  fi
}

# log, and also speak on stdout so a manual run is not silent (cron captures it).
say() { log "$*"; [ "$DRY_RUN" = 1 ] && return 0; printf '%s\n' "$*"; }

# Log a line only when it differs from the last one logged under the same key.
#
# A tick with nothing to do says the same things every ten minutes: the
# configuration, the renewed claim, a pull request still waiting. Written each
# time they bury the line that changed, and a log read for a fault is read for
# what is different. The key names the line, not the tick, so a line that
# changes is logged at once and one that comes back after a different answer is
# logged again; callers drop a key whose line should not survive a failure.
#
# A suppressed repeat still touches the log. The idle display reports the age of
# the log as proof that the driver ticks, and a quiet log must not read as a dead
# driver. The remembered line is one file per key in the driver's own folder, so
# it is untracked and goes with the rest of the run state.
log_changed() {
  local key="$1" line="$2" file="$RUN_DIR/log-last-$1"
  if [ "$DRY_RUN" != 1 ]; then
    if [ "$(cat "$file" 2>/dev/null)" = "$line" ]; then
      touch "$LOG"
      return 0
    fi
    printf '%s\n' "$line" >"$file"
  fi
  log "$line"
}

# One line out of text the selector wrote, safe to put in a log entry.
#
# The reason a selector refuses is free text from git and from the reservation
# code, and the real one is a wrapped git message. The log holds one entry per
# line, so a newline inside the reason would forge a second entry and a carriage
# return would rewrite the entry above it. Everything else printable is left
# alone, so a path or a branch name inside the reason survives for whoever reads
# it.
#
# The rendering is one way and cannot be read back. A real newline becomes a
# backslash and an n, and a reason that already held exactly those two characters
# comes out identical, so the flattened text is faithful to a reader and useless
# to a parser. That is deliberate: nothing reads it back, and the whole uncapped
# answer is written to a file beside the log line for anything that needs the
# text itself.
#
# The replacement has to be written as dollar-ansi quotes holding one backslash
# and an n. Measured on bash 3.2: dollar-ansi gives one backslash, single quotes
# around a backslash pair give two, so the log would read a doubled separator.
# Measured on bash 3.2: a bare backslash-n also gives one, but bash 5.2 and later
# rewrite an unquoted ampersand in a replacement into the text that matched, so
# the replacement stays quoted rather than relying on the version we run on.
flatten_one_line() {
  local s=${1-}
  s=${s//$'\r'/}
  s=${s//$'\n'/$'\\n'}
  s=${s//[[:cntrl:]]/ }
  printf '%s' "$s"
}

# The same one line, cut at a limit, saying how much was dropped.
#
# The limit is 500 bytes, and the number is this driver's own choice rather than
# a published one: it is picked so a single refusal stays readable in a log tail,
# and it costs nothing because the whole uncapped answer goes to a file beside
# the line. The part of this that has guidance behind it is the sanitising above,
# not the length: carriage return, line feed and delimiters are the characters a
# log reader parses.
#
# Bytes, not characters, and never half a character. The shell counts characters
# when its locale is multibyte and bytes when it is not, and cron hands this
# driver no locale at all, so a count left to the ambient one is 500 characters on
# one machine and 500 bytes on the next. The count and the cut are therefore taken
# under a function-local C locale, which makes them bytes everywhere and leaves
# the driver's own locale as it was: measured on bash 3.2, a string of two ASCII
# letters and one three-byte character counts as 7 outside this function, 9 inside
# it, and 7 again once it has returned.
#
# A byte cut lands inside a character whenever one straddles that point, and half
# a character in a log is a byte sequence the reader's terminal cannot decode. The
# cut is therefore walked back to the last whole character: trailing continuation
# bytes go, and a lead byte left without them goes too. The byte value comes from
# printf's own quoted-character form, and all 256 values were classified under a C
# locale, this machine's locale and an explicit UTF-8 one on bash 3.2: 128 ASCII,
# 64 continuation, 64 lead, none left over. The same form reads a whole character
# as its code point under a multibyte locale, which is why the slice is taken in
# the C locale rather than in whatever the driver was handed.
#
# The dropped count is in the line because a bare cut reads as a whole reason when
# it is half of one. It counts bytes, because the cut did. A cut landing between
# the backslash and the n of a flattened newline leaves one bare backslash before
# the marker; that is left alone on purpose, because the entry is read by a person
# and a backslash before a marker is legible rather than broken.
#
# Neither argument is required. This runs inside an unattended tick, so a caller
# that forgets one must cost a line of log and not the tick, which is why this
# takes a default for each where the sibling above already took one.
cap_length() {
  local s=${1-} max=${2-} LC_ALL=C cut last byte
  if (( ${#s} > max )); then
    cut="${s:0:max}"
    while [ -n "$cut" ]; do
      cap_last_byte || break
      # 128..191 is a continuation byte: 10xxxxxx, and the character it belongs to
      # starts earlier, so a cut that lands on one is always mid-character.
      (( byte >= 128 && byte <= 191 )) || break
      cut="${cut%?}"
    done
    if [ -n "$cut" ]; then
      # 192..255 is a lead byte: 11xxxxxx. One of those is the first byte of a
      # character whose remaining bytes were cut away, so it goes too.
      cap_last_byte && (( byte >= 192 )) && cut="${cut%?}"
    fi
    printf -v s '%s...[+%d bytes truncated]' "$cut" "$(( ${#s} - ${#cut} ))"
  fi
  printf '%s' "$s"
}

# Read the last byte of `cut` into `byte`, as a value from 0 to 255, and return 0.
#
# The sign is the whole difficulty, and it changed in bash 4. `printf '%d' "'X"`
# reports a byte with the high bit set as a NEGATIVE number on bash 3.2 and as
# the UNSIGNED value on bash 4 and later: measured here, the first byte of a
# three-byte character comes back as -28 on bash 3.2.57 and as 228 on bash 5.3.20,
# from the same script and the same locale. Testing the sign therefore passes on
# one version and silently does nothing on the other, which is how this function
# came to emit half a character on every machine running a current bash while
# passing on the machine that reported it.
#
# So the value is normalised to 0..255 here and every caller tests unsigned
# thresholds. `cut` must be the variable name: the caller sets it.
cap_last_byte() {
  last="${cut: -1}"
  printf -v byte '%d' "'$last" || return 1
  (( byte < 0 )) && byte=$(( byte + 256 ))
  return 0
}
