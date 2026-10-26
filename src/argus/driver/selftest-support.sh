#!/usr/bin/env bash
# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The tripwire half of the phase driver self-test support: it snapshots the
# machine's real checkouts and run state so a run that reached them says so.
# Sourced by argus/driver/phase-driver.sh; not executable on its own.

# --- the tripwire ----------------------------------------------------------

# One line describing a checkout: its branch, its HEAD, and the paths its working
# tree reports as changed. Space-separated, never empty, and always the same
# shape, so two snapshots can be compared with `=` alone.
#
# A checkout that cannot be read answers the word `unreadable` rather than an
# empty string, because two unreadable checkouts would otherwise compare equal
# and a tripwire that cannot see is not a tripwire.
#
# Changed PATHS and not a count of them. A count cannot name what changed, so the
# failure message cannot say whether the run did it; and a count is moved by
# anything else on the machine. Measured: a database file written into the public
# checkout by an unrelated service turned a clean tree into one dirty path and
# failed this tripwire on a run that had touched nothing. Naming the paths makes
# the same event legible instead of mysterious.
#
# Tracked paths only. An untracked file appearing is far more likely to be some
# other process than this suite, and a check that fails on that is a check
# people learn to ignore. What this suite could plausibly do to the real
# checkouts is move a branch, move HEAD, or edit a tracked file; an untracked
# write is covered by the run-state check below, which is the case that has
# actually happened here.
selftest_repo_state() {
  local repo="$1" branch head changed
  # An extracted source tree needs no project checkout to run its self-test.
  # A missing checkout is distinct from a checkout that exists but cannot be read.
  if [ ! -e "$repo/.git" ]; then
    printf 'absent'
    return 0
  fi
  if ! branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
    printf 'unreadable'
    return 0
  fi
  head="$(git -C "$repo" rev-parse HEAD 2>/dev/null || printf 'no-head')"
  # Sorted so two snapshots of the same state are the same string, and folded to
  # one line so the whole state is one comparable value.
  changed="$(git -C "$repo" status --porcelain=v1 --untracked-files=no 2>/dev/null \
    | LC_ALL=C sort | tr '\n' ';' || true)"
  printf '%s %s changed=[%s]' "$branch" "$head" "$changed"
}

# The commit the board's branch points at, read from the local
# remote-tracking ref rather than from the network.
#
# This is the ref that catches a reservation taken against a real board. The
# board is fetched with a refspec onto `refs/remotes/<remote>/<branch>` before
# every read-modify-write, so a real `reserve` moves this ref locally without
# any network call and without any working-tree change at all. Reading it is
# therefore both cheap enough for an exit trap and the one signal that catches
# a claim taken against the wrong board.
selftest_board_state() {
  local private="$1" remote="$2" branch="$3" ref
  ref="refs/remotes/$remote/$branch"
  if git -C "$private" rev-parse --verify -q "$ref" >/dev/null 2>&1; then
    git -C "$private" rev-parse "$ref" 2>/dev/null || printf 'unreadable'
  else
    printf 'no-such-ref'
  fi
}

# Record the machine's real state. Called once, before the suite points itself
# anywhere. Every value it sets is global and unset nowhere, because the exit
# trap reads them after `driver_self_test` has returned.
selftest_real_state_capture() {
  SELFTEST_REAL_PUBLIC="$REPO"
  SELFTEST_REAL_PRIVATE="${BOARD_REPO:-}"
  SELFTEST_REAL_RUNS="$WF"
  SELFTEST_REAL_PUBLIC_STATE="$(selftest_repo_state "$SELFTEST_REAL_PUBLIC")"
  SELFTEST_REAL_PRIVATE_STATE="$(selftest_repo_state "$SELFTEST_REAL_PRIVATE")"
  SELFTEST_REAL_BOARD_STATE="$(selftest_board_state \
    "$SELFTEST_REAL_PRIVATE" origin "${PHASE_COORDINATION_BRANCH:-}")"
  # A reference point in time, for asking which run-state files are newer than
  # the moment this run started. `find -newer` needs a file, and an empty
  # temporary one is the cheapest thing that carries an mtime.
  SELFTEST_REAL_RUNS_MARKER="$SELFTEST_TMP/real-runs-marker"
  : >"$SELFTEST_REAL_RUNS_MARKER" 2>/dev/null || SELFTEST_REAL_RUNS_MARKER=""
}

# Print one line per way the machine's real state differs from what this run
# found, and return non-zero if there is at least one. Prints nothing and
# returns 0 when everything matches.
#
# This runs from the exit trap rather than from the end of the body, because
# the case it exists for is a run that stops half way. A check at the end of
# the body is not run at all by the run that needs it, which is the same blind
# spot this suite was reported for twice.
selftest_real_state_violations() {
  local now_public now_private now_board touched violations=0
  now_public="$(selftest_repo_state "$SELFTEST_REAL_PUBLIC")"
  now_private="$(selftest_repo_state "$SELFTEST_REAL_PRIVATE")"
  now_board="$(selftest_board_state \
    "$SELFTEST_REAL_PRIVATE" origin "${PHASE_COORDINATION_BRANCH:-}")"
  if [ "$now_public" = unreadable ] || [ "$now_private" = unreadable ]; then
    printf 'the checkout tripwire could not read a checkout (public %s, private %s)\n' \
      "$now_public" "$now_private"
    violations=$((violations + 1))
  else
    if [ "$now_public" != "$SELFTEST_REAL_PUBLIC_STATE" ]; then
      printf "the public checkout moved (found '%s', now '%s')\n" \
        "$SELFTEST_REAL_PUBLIC_STATE" "$now_public"
      violations=$((violations + 1))
    fi
    if [ "$now_private" != "$SELFTEST_REAL_PRIVATE_STATE" ]; then
      printf "the private checkout moved (found '%s', now '%s')\n" \
        "$SELFTEST_REAL_PRIVATE_STATE" "$now_private"
      violations=$((violations + 1))
    fi
  fi
  if [ "$now_board" != "$SELFTEST_REAL_BOARD_STATE" ]; then
    printf "the shared board moved (found '%s', now '%s')\n" \
      "$SELFTEST_REAL_BOARD_STATE" "$now_board"
    violations=$((violations + 1))
  fi
  if [ -n "$SELFTEST_REAL_RUNS_MARKER" ] && [ -d "$SELFTEST_REAL_RUNS" ]; then
    # No `head` in this pipeline. `head` closing the pipe early sends find a
    # SIGPIPE, and under `pipefail` that turns a successful search into a
    # failing one, which is the opposite of what a tripwire wants. The output
    # is capped by width instead.
    touched="$(find "$SELFTEST_REAL_RUNS" -type f -newer "$SELFTEST_REAL_RUNS_MARKER" \
      2>/dev/null)" || touched=""
    if [ -n "$touched" ]; then
      printf 'run state was written: %s\n' \
        "$(printf '%s' "$touched" | tr '\n' ' ' | cut -c1-600)"
      violations=$((violations + 1))
    fi
  fi
  [ "$violations" -eq 0 ]
}

# --- properties this suite depends on --------------------------------------

# These live in the repository rather than in a scratch directory because they
# are the evidence for two claims the code makes in comments. A comment that
# says a construct is safe on every bash is only worth reading if the suite
# checks it, and a check whose evidence sits in an uncommitted scratch file is
# not a check anyone else can run.

# The guarded empty-array expansion.
#
# The driver copies arrays under `set -u`, and on bash older than 4.4 an
# expansion of an empty array is an unbound-variable error. `"${a[@]+"${a[@]}"}"`
# is the form that means "expand it if it is set" on every version, and this
# asserts that rather than assuming it: the plain form must agree with the
# guarded one for every shape, and the guarded form must survive `set -u` on an
# array that is empty.
selftest_property_array_idiom() {
  local rc=0
  # Four shapes, one per call, written out rather than looped over a table: an
  # array holding no elements, one holding a single empty element, one whose
  # element contains a space, and one whose element looks like a glob. A table
  # would have to be split by a delimiter, and that split would expand the glob.
  selftest_idiom_probe empty || return 1
  selftest_idiom_probe one-empty || return 1
  selftest_idiom_probe spaced || return 1
  selftest_idiom_probe globbed || return 1

  # And the array form the driver actually uses must survive `set -u` when the
  # array is empty, which is the whole reason the guarded form is used. It is a
  # separate statement from the probes above because the probes compare the two
  # expansions with `set -u` off in the plain arm, since the plain arm aborts on
  # bash older than 4.4 and a comparison cannot be made against an abort.
  #
  # In a child, so that if this ever does abort it aborts the child and not the
  # suite that is checking it.
  ( set -u; local -a empty=(); printf '%s' "${empty[@]+"${empty[@]}"}" ) \
    >/dev/null 2>&1 || rc=$?
  [ "$rc" -eq 0 ]
}

# Do the plain and the guarded expansion of one shape agree?
#
#   $1  the shape's name
#
# The elements are set with `set --` rather than declared as an array and passed
# in, for a reason that is the whole difficulty here: naming an empty array as an
# argument expands it at the call site, and under `set -u` on bash older than
# 4.4 that expansion is precisely the error being measured. `"${@+"$@"}"` is
# the same guarded-expansion construct as `"${a[@]+"${a[@]}"}"`, differing only
# in that `@` yields parameters where `[@]` yields elements.
selftest_idiom_probe() {
  local label="$1" plain guarded
  case "$label" in
    empty)     set -- ;;
    one-empty) set -- "" ;;
    spaced)    set -- "a b" "c" ;;
    globbed)   set -- "*" "b*" ;;
    *) printf '  unknown shape %s\n' "$label" >&2; return 1 ;;
  esac
  plain="$(printf '%s\n' "$@" 2>&1)" || plain="<status $?>"
  guarded="$(printf '%s\n' "${@+"$@"}" 2>&1)" || guarded="<status $?>"
  if [ "$plain" = "$guarded" ]; then
    return 0
  fi
  printf '  %s: plain [%s] guarded [%s]\n' "$label" \
    "$(printf '%s' "$plain" | tr '\n' ',')" \
    "$(printf '%s' "$guarded" | tr '\n' ',')"
  return 1
}

# The reached-end flag, as a property.
#
# The bug this closes was a self-test that stopped half way and exited 0, and
# its cause is not portable: on bash 3.2 an unbound-variable abort under
# `set -euo pipefail` exits 0 when an EXIT trap is installed and 1 when one is
# not, and a trap that re-exits with the saved status still exits 0. On bash 5
# the same body exits 1 either way. So the status cannot be the signal on any
# version, and the flag is the only mechanism that works on all of them.
#
# What is asserted here is therefore the fix and not the environment: given a
# run that chose status 0 without reaching its last check, the flag must turn
# that into a failure. That holds on every bash, which an assertion about the
# environment's own status would not.
selftest_property_reached_end_flag() {
  local rc=0
  SELFTEST_REACHED_END=0
  if [ 0 -eq 0 ] && [ "$SELFTEST_REACHED_END" -eq 0 ]; then rc=1; fi
  [ "$rc" -ne 0 ] || return 1
  rc=0
  SELFTEST_REACHED_END=1
  if [ 0 -eq 0 ] && [ "$SELFTEST_REACHED_END" -eq 0 ]; then rc=1; fi
  [ "$rc" -eq 0 ]
}
