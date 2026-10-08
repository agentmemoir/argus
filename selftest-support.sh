#!/usr/bin/env bash
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
#
# Hermetic-run support for the phase driver's self-test.
#
# Sourced by argus/phase-driver.sh. Not executable on its own. Expects the
# driver to have already set: REPO, BOARD_DIR, WF, ARGUS, PYTHON,
# PHASE_MACHINE_ID, PHASE_COORDINATION_BRANCH, and to run under `set -euo pipefail`.
#
# WHY THIS IS SEPARATE FROM THE DRIVER
#
# This suite used to run its cases against the machine's real checkouts and
# guard against reaching them with a stub and with a check at the end. Both
# guards failed. The stub skipped the code that moves a checkout, so the
# checkout-moving step was untested; the check sat at the end of the body, so a
# run that stopped half way never reached it. The fix is not a better guard: it
# is a run that has nowhere to reach. `selftest_fixture_root` builds a throwaway
# public checkout, private checkout and board, and the suite points itself at
# that before the first case runs. The check then stays, because a check that
# the layout makes unnecessary is still the thing that tells you the layout
# stopped being honest.

# --- the throwaway checkout ------------------------------------------------

# Build a complete, disposable pipeline checkout: a bare public remote and a
# bare private remote, a clone of each, the driver's own code, a roadmap, a
# workflow file, run state, and a real reservation on the fixture board for this
# host.
#
#   $1  the root the driver will be pointed at   (created)
#   $2  a scratch folder for the bare remotes    (created)
#
# Returns 0 on success. On failure prints the reason to stderr and returns 1,
# so a caller that ignores the status is choosing to run cases against a root
# that does not exist, which is worse than not running them.
selftest_fixture_root() {
  local root="$1" base="$2"
  mkdir -p "$base" || return 1
  git init -q --bare "$base/pub.git" || return 1
  git -C "$base/pub.git" symbolic-ref HEAD refs/heads/master || return 1
  git init -q --bare "$base/priv.git" || return 1
  git -C "$base/priv.git" symbolic-ref HEAD refs/heads/master || return 1
  git clone -q "$base/pub.git" "$root" || return 1
  printf '%s/\n' "$BOARD_DIR" >>"$root/.git/info/exclude"
  git -C "$root" config user.email f@e.com
  git -C "$root" config user.name fixture
  git -C "$root" checkout -q -b master
  printf 'one\n' >"$root/a.txt"
  git -C "$root" add -A
  git -C "$root" commit -q -m init
  git -C "$root" push -q -u origin master
  mkdir -p "$root/$BOARD_DIR"
  git clone -q "$base/priv.git" "$root/$BOARD_DIR" || return 1
  git -C "$root/$BOARD_DIR" config user.email f@e.com
  git -C "$root/$BOARD_DIR" config user.name fixture
  git -C "$root/$BOARD_DIR" checkout -q -b master
  printf 'one\n' >"$root/$BOARD_DIR/b.txt"
  git -C "$root/$BOARD_DIR" add -A
  git -C "$root/$BOARD_DIR" commit -q -m init
  git -C "$root/$BOARD_DIR" push -q -u origin master
  # The driver's own code, because the tmux helpers look for the idle script
  # beside the driver, and because the tools that select and claim work now
  # live beside it too: a root without them cannot open a phase window, and
  # the failure would be reported as a launch failure rather than a sync one.
  mkdir -p "$root/$BOARD_DIR/argus"
  cp -R "$ARGUS/." "$root/$BOARD_DIR/argus/" || return 1
  find "$root/$BOARD_DIR/argus" -name '__pycache__' -type d -prune \
    -exec rm -rf {} + 2>/dev/null || true
  # The private checkout ignores bytecode, so the fixture must too or the
  # tick's own imports show up as dirt.
  printf '__pycache__/\n' >"$root/$BOARD_DIR/.gitignore"
  # A roadmap, because the selection path reads it to find the phase file, and
  # the workflow folder, because the driver refuses a tick whose pipeline file
  # is missing rather than inventing one. A fixture that omitted it would have
  # tested the refusal instead of the launch.
  mkdir -p "$root/$BOARD_DIR/plans" "$root/$BOARD_DIR/history/phase-100010" \
    "$root/$BOARD_DIR/argus/bin"
  cat >"$root/$BOARD_DIR/argus/bin/glide" <<'SH'
#!/usr/bin/env bash
exit 0
SH
  chmod +x "$root/$BOARD_DIR/argus/bin/glide"
  # This pipeline needs no project scripts or engine source checkout.
  cat >"$root/$BOARD_DIR/flow.yaml" <<YAML
version: 1
run_dir: $BOARD_DIR/history/phase-{phase_number:06d}
inputs:
  phase_number: {type: int, required: true}
  phase_file: {type: str, default: ""}
start: fixture
ends: [completed]
steps:
  fixture:
    stage: fixture.md
    record_as: FIXTURE
    bindings: {}
    end: completed
YAML
  cat >"$root/$BOARD_DIR/fixture.md" <<'STAGE'
---
name: Fixture
harness: hermes:fixture
placeholders: {}
---
Fixture stage.
STAGE
  printf '# 100010. Fixture\n' \
    >"$root/$BOARD_DIR/plans/phase-100010-fixture.md"
  printf '{"phase": "100010", "state": "blocked"}\n' \
    >"$root/$BOARD_DIR/history/phase-100010/run.json"
  # Commit only the fixture's own scaffolding. The phase's work above must stay
  # staged and uncommitted, which is the whole point of the stranding case: a
  # `git add -A` here would commit the review baseline and leave a clean index.
  git -C "$root/$BOARD_DIR" add argus plans history flow.yaml fixture.md .gitignore
  git -C "$root/$BOARD_DIR" commit -q -m "driver, roadmap and run state"
  git -C "$root/$BOARD_DIR" push -q origin master
  # A real claim for this host, so a tick takes the real coordination path.
  # Stubbing coordination would skip the sync, which is the code under test.
  #
  # The private checkout is named explicitly rather than inherited: the driver
  # exports its own for the real machine, and a fixture that let that reach a
  # `reserve` call would take a claim on the real shared board while building a
  # throwaway one.
  #
  # The machine id is this host's own rather than a fixed name, because the
  # cases set own-claim recovery and a recovery only fires for a claim this
  # machine owns. A fixed name made the claim someone else's on every host but
  # one, so the tick answered "wait for the claim" where the case expected a
  # launch, and the case passed or failed according to which machine ran it.
  # The board's own branch is named so the fixture never touches the real one.
  # It has to be the branch the code defaults to, not the integration branch: a
  # fixture whose board shared a branch with run state would test a layout the
  # driver no longer runs, and the board's default is asserted against this
  # value, so a name that drifts from it fails the suite rather than passing it.
  local reserve_out fixture_config="$base/fixture-config.json"
  selftest_write_config "$root" "$fixture_config" "$BOARD_DIR" || return 1
  reserve_out="$(cd "$root" && \
      "$PYTHON" "$root/$BOARD_DIR/argus/phase_reservations.py" \
      reserve --config "$fixture_config" --phase 100010 \
      --machine-id "$PHASE_MACHINE_ID" 2>&1)" \
    || { printf '    reserve failed: %s\n' "$reserve_out" >&2; return 1; }
  git -C "$root/$BOARD_DIR" fetch -q origin
  git -C "$root/$BOARD_DIR" merge -q --ff-only origin/master
}

# Put a fixture into the state a merged-then-deleted phase branch leaves behind:
# the public checkout sitting on a phase branch whose remote copy is gone, with a
# publication receipt saying the phase is published.
#
#   $1  the root      $2  the scratch folder holding the bare remotes
#   $3  the phase branch name to sit on
selftest_fixture_strand_on_published_branch() {
  local root="$1" base="$2" branch="$3" remote
  remote="$base/pub.git"
  # A second, unfinished phase, because a fixture whose only phase is the
  # published one offers the selector nothing and the tick stops with NO_PHASE
  # before it reaches the code under test. The reported stall had a next phase
  # waiting, and a case that cannot reproduce that is testing something else.
  printf '# 100011. Fixture, not done\n- [ ] Work to do.\n' \
    >"$root/$BOARD_DIR/plans/phase-100011-fixture-next.md"
  git -C "$root" checkout -q -b "$branch"
  printf 'phase work on %s\n' "$branch" >"$root/phase.txt"
  git -C "$root" add phase.txt
  git -C "$root" commit -q -m "phase: work for $branch"
  git -C "$root" push -q -u origin "$branch"
  # The merge, done the way GitHub does it: the branch's content lands on the
  # integration branch and the branch itself is deleted. Nothing here asks
  # whether the merge is real, because the receipt below is what records that,
  # and it is written by the merge check rather than by this fixture.
  git -C "$root" checkout -q master
  git -C "$root" merge -q --ff-only "$branch"
  git -C "$root" push -q origin master
  git -C "$remote" branch -D "$branch" >/dev/null 2>&1
  # Back onto the dead branch, which is where finalize leaves the checkout.
  #
  # The fetch is deliberately NOT --prune. That is the state the reported stall
  # was in: the local remote-tracking ref still exists, so the checkout's
  # upstream resolves and the sync reaches its own fetch -- which prunes, finds
  # the ref gone, and reports a missing upstream. A fixture that pruned here
  # would produce a different and easier refusal ("branch has no upstream"),
  # and a fix aimed at the first would pass on the second without having been
  # tested against the case that stopped the line.
  git -C "$root" checkout -q "$branch"
  git -C "$root" fetch -q origin
  git -C "$root/$BOARD_DIR" fetch -q origin
  # A receipt in the shape the reader accepts. The version field is not
  # decoration: publication_receipt_done requires it, so a receipt without it
  # leaves the phase unfinished, the selector offers it again, and the case would
  # be asserting recovery on a fixture that never held the published state at
  # all.
  printf '{"version": 1, "phase": "100010", "state": "published", "branch": "%s", "private_branch": "master", "integration_branch": "master"}\n' \
    "$branch" >"$root/$BOARD_DIR/history/phase-100010/publication.json"
  git -C "$root/$BOARD_DIR" add history/phase-100010/publication.json \
    plans/phase-100011-fixture-next.md
  git -C "$root/$BOARD_DIR" commit -q -m "runs: record phase 100010 as published"
  git -C "$root/$BOARD_DIR" push -q origin master
}

# Add the stranding case's extra state to a fixture the caller already built: a
# staged baseline and an unstaged fix in each checkout, plus a clean commit from
# another developer pushed to each remote and touching a different path.
#
#   $1  the root      $2  the scratch folder holding the bare remotes
selftest_fixture_stage_work() {
  local root="$1" base="$2" pair remote target other
  printf 'phase baseline\n' >"$root/$BOARD_DIR/phase.txt"
  git -C "$root/$BOARD_DIR" add phase.txt
  printf 'phase baseline\nremediator fix\n' >"$root/$BOARD_DIR/phase.txt"
  printf 'phase work\n' >"$root/phase.txt"
  git -C "$root" add phase.txt
  for pair in "$base/pub.git:$root/a.txt" "$base/priv.git:$root/$BOARD_DIR/b.txt"; do
    remote="${pair%%:*}"; target="${pair#*:}"
    other="$base/other-$(basename "$remote")"
    git clone -q "$remote" "$other"
    git -C "$other" config user.email o@e.com
    git -C "$other" config user.name other
    printf 'two\n' >>"$other/$(basename "$target")"
    git -C "$other" add -A
    git -C "$other" commit -q -m "other: touch $(basename "$target")"
    git -C "$other" push -q origin master
    rm -rf "$other"
  done
}

# Write a complete configuration file for a fixture checkout.
#
# The suite runs the same configuration path a real tick does: the file names
# the fixture's own checkout, pipeline, roadmap, run state and board, and the
# driver reads it with the module rather than from the shell's surroundings.
# Written for each fixture root, because a sub-fixture that repoints the driver
# launches children, and those children must read the sub-fixture too.
#
#   $1  the fixture root          (repo-root; the file is written elsewhere)
#   $2  the config file to write  (its folder is not the root, so paths are
#                                  absolute and resolve as written)
#   $3  the board folder name inside the root
#   $4  optional board checkout; $5 optional board file; $6 optional branch
selftest_write_config() {
  "$PYTHON" - "$1" "$2" "$3" "${4:-}" "${5:-claims.json}" \
      "${6:-fixture-claims}" "$PHASE_MACHINE_ID" <<'PY'
import json
import sys
from pathlib import Path

root, config, board_dir, board_repo, board_file, board_branch, machine_id = sys.argv[1:]
root = Path(root).resolve()
board_dir = Path(board_dir)
board = Path(board_repo).resolve() if board_repo else (root / board_dir).resolve()
doc = {
    "repo-root": str(root),
    "pipeline": str(root / board_dir / "flow.yaml"),
    "roadmap-dir": str(root / board_dir / "plans"),
    "number-input": "phase_number",
    "file-input": "phase_file",
    "driver": {
        "provider": "phase", "include": ["phase-*.md"],
        "runs": str(root / board_dir / "history"), "state": "driver-state",
        "stale-after": 2700, "outage-stale": 32400, "quiet-minutes": 45,
        "keep-logs-days": 7, "merge-check-max": 5,
        "recover-own-claim": False,
    },
    "machine": {"id-file": str(config) + ".machine-id"},
    "commands": {
        "glide": "glide", "git": "git", "tmux": "tmux",
        "python": sys.executable, "hermes": "hermes", "gh": "gh",
    },
    "tmux": {
        "session": "development", "width": 164, "height": 56,
        "idle-window": "idle", "phase-window": "phase",
        "idle-script": "phase-tmux-idle.sh", "refresh": 15,
    },
    "board": {
        "repo": str(board), "file": board_file,
        "branch": board_branch, "remote": "origin",
    },
}
Path(str(config) + ".machine-id").write_text(machine_id + "\n", encoding="utf-8")
Path(config).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
}

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
