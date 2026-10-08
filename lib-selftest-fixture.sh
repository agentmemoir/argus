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

