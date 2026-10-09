#!/usr/bin/env python3
# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed git sync for the public and private checkouts.

Before a phase starts, and again before finalize publishes, the pipeline
must not run on a stale or diverged base. This module fetches each checkout
from its own configured upstream and brings it in line by fast-forward or a
plain merge, stopping the line only when it cannot do so safely.

The configured run directory must be inside the private checkout for
publication, because phase receipts and run state are committed there.

Two modes, because "local is ahead" means different things:

* ``start`` (before a phase): bring the checkout in line with its remote.
  A strictly-behind branch is fast-forwarded; a diverged branch is merged
  with a plain merge commit that keeps every local commit and accepts the
  remote changes. Local commits ahead of the remote are fine here: the
  remote has nothing new, so nothing is pulled and those commits publish at
  finalize.
* ``push`` (before finalize pushes): the phase's own commits are local, so
  an ahead branch is verified as a fast-forward push. A diverged or behind
  remote is merged (or fast-forwarded) first so the push can proceed; a
  conflict is left in place (not aborted) for the finalize agent to resolve.

``commit_leftover_run_state`` is the fresh-start cleanup: when given a phase,
it commits and pushes only that phase's private ``runs/phase-<N>`` leftovers
(the runner writes ``ledger.json``/``run.json`` after finalize's commit) and
refuses staged paths outside that phase. ``--mode publish`` additionally
checks the phase's three-part reservation fence, takes the short publication
lock, verifies effective public/private Git push destinations, writes and pushes
``runs/phase-<N>/publication.json``, and performs the final sync-and-push
without force.

Hard safety rules, enforced by construction:

* Never ``git pull`` (this repo sets ``pull.rebase = true``, which would
  rewrite history), never rebase, never force-push, never reset, never
  stash, never ``clean``. Fast-forward when possible, otherwise a plain
  merge commit; a real conflict is aborted in start mode.
* Unreviewed work is never at risk from a sync attempt. Every precondition is
  tested *before* git is asked to change anything, so a refusal is always
  reached with the index and the working tree exactly as they were.
* A dirty index is not a reason to stop on its own. A phase stages its work
  and never commits it, so the index is dirty for the whole life of a phase
  and a blanket refusal strands any phase whose remote moved. What stops an
  integration is an *overlap* between the paths the incoming commits change
  and the paths local work has touched. How much overlap is fatal depends on
  the relation, because git's own guard does:

  - strictly behind (a fast-forward): git carries a dirty index, staged or
    not, provided the incoming commits do not touch the same paths. So the
    overlap test is the whole gate. Measured on git 2.53.0.
  - diverged (a real merge): git refuses to merge at all while the index
    differs from HEAD, whether or not the paths are disjoint, and the refusal
    is the same under every merge strategy. Measured on git 2.53.0. So the
    index stays a hard gate here, and only unstaged and untracked work is
    allowed through, and only when it does not overlap.

  Both were re-measured on the toolchain this pipeline runs before either
  rule was written. Treat them as measurements, not as guarantees, and
  re-measure if git changes: the fast-forward case is the one that would
  silently start failing, and it fails safe (git refuses, we report it).
* Giving up is the last resort: a mechanical fast-forward or a clean merge
  is preferred. Only a genuine conflict, an overlapping local edit, or a
  detached HEAD stops the line, and then local work is reported, never
  discarded.
* The configured private checkout is synced only from its own
  remote; effective public/private fetch and push destinations are compared,
  so private content cannot cross into the public checkout.

Usage (name the source tool and the project root):

    python3 /path/to/argus/gitsync.py --root /path/to/project --mode start
    python3 /path/to/argus/gitsync.py --root /path/to/project --mode push
    python3 /path/to/argus/gitsync.py --root /path/to/project --mode publish --phase 100440
    python3 /path/to/argus/gitsync.py --self-test

Exit codes: 0 synced/verified, 1 blocked (operator decision required),
2 config or usage error. JSON goes to stdout in every case.
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

# Source commands import the package from their own folder.
if not __package__:
    import _bootstrap  # noqa: F401 - register the source package
from argus import conventions
from argus.config import ConfigError as ArgusConfigError
from argus.conventions import (
    board_repo,
    phase_file_parts,
    phase_paths,
    phase_run_dir,
    runs_dir,
    setting,
)

# A fixture path only; production paths come from the loaded config.
BOARD_REL = Path("vault") / "notes"
FETCH_TIMEOUT_S = 180
GIT_TIMEOUT_S = 60
# gh answers one pull request in well under a second — measured locally at
# roughly 0.13s per phase end to end, including git, on a warm local remote. The
# bound is therefore set by what a tick can afford, not by how slow gh is: the
# driver runs from cron every few minutes, checks up to CLIO_MERGE_CHECK_MAX
# phases per tick, and must finish inside that interval. At 15s and a cap of 5
# the worst case is about 75 seconds, and the cap is reported when it is hit
# rather than silently truncating the check.
GH_TIMEOUT_S = 15
MAX_SUBJECTS = 5

# The integration branch: the one a phase's pull request targets, and the one
# nothing a phase writes may land on. Resolved per checkout rather than
# hard-coded, because a repository's integration branch is a fact about that
# repository and this value is only the expected name for it.
INTEGRATION_BRANCH = "master"


def integration_branch(repo, default=INTEGRATION_BRANCH):
    """The integration branch for a checkout.

    Prefers ``default`` when the checkout actually has it, so a host that
    renamed its integration branch does not silently get a second one. Falls
    back to the remote's own default branch, which is what a fresh clone
    reports.
    """
    repo = Path(repo)
    if is_git_repo(repo):
        rc, _, _ = git(repo, "rev-parse", "--verify", default)
        if rc == 0:
            return default
        rc, out, _ = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if rc == 0 and out:
            return out.rsplit("/", 1)[-1]
    return default


# A phase branch is named from the behaviour in the phase's roadmap file, and
# never from its number. The branch is pushed to the public repository, where
# the number reads as an internal label to every reviewer, and the behaviour is
# the part that says what the change carries. `phase-100440-backup-archive-
# format-and-directory.md` becomes `feature/backup-archive-format-and-
# directory`. A file carrying no behaviour is refused, and the number is still
# checked against the phase the caller means, so the wrong roadmap file cannot
# put a phase's work on another phase's branch.
PHASE_BRANCH_PREFIX = "feature/"


def _phase_file_parts(roadmap_name):
    """Split a roadmap file's base name into its number and its behaviour.

    Args:
        roadmap_name: A roadmap file's base name, with or without ``.md``.

    Returns:
        ``(number, behaviour)``, for example
        ``("100440", "backup-archive-format-and-directory")``.

    Raises:
        ConfigError: The name is not a phase file, or carries no behaviour.
    """
    name = str(roadmap_name or "")
    parts = phase_file_parts(name if name.endswith(".md") else name + ".md")
    if parts is None:
        raise ConfigError(
            f"cannot derive a phase branch from {roadmap_name!r}: "
            "expected a configured phase filename with a behaviour"
        )
    number, behaviour = parts
    if not behaviour:
        raise ConfigError(f"phase file carries no behaviour: {roadmap_name!r}")
    return str(number), behaviour


def phase_branch(roadmap_name):
    """The branch name for a phase, derived from its roadmap file name.

    Args:
        roadmap_name: A roadmap file's base name, with or without ``.md``.

    Returns:
        The branch name, for example ``feature/backup-archive-format``. The
        phase number is deliberately absent: this branch is published to the
        public repository.

    Raises:
        ConfigError: The name is not a phase file, or carries no behaviour.
    """
    _number, behaviour = _phase_file_parts(roadmap_name)
    return f"{PHASE_BRANCH_PREFIX}{behaviour}"


def phase_branch_for(phase, roadmap_name):
    """The branch for a phase, checked against the phase number the caller
    expects. A mismatch means the wrong roadmap file was passed, which would
    put the phase's work on another phase's branch.

    The number is read from the file name rather than from the branch, because
    the branch no longer carries it.
    """
    number, _behaviour = _phase_file_parts(roadmap_name)
    if int(number) != int(phase):
        raise ConfigError(
            f"roadmap file {roadmap_name!r} belongs to phase {int(number):06d}, "
            f"not {int(phase):06d}; refusing to guess a branch name"
        )
    return phase_branch(roadmap_name)


def roadmap_folder(root, configured=None):
    """The roadmap folder to scan, as a Path.

    A folder named by the caller is taken as the operator's word and is never
    silently replaced by the derived one: the branch-uniqueness check must look
    at the same phases the selector chose from, or a collision inside a
    configured folder is invisible to it. A relative value resolves against the
    repository root, matching the selector's own rule and the driver's shell.
    """
    configured = configured or setting("roadmap-dir")
    if configured:
        base = Path(configured)
        return base if base.is_absolute() else Path(root) / base
    return board_repo(root) / "roadmap"


#: The marker a group branch carries between the prefix and its lowest member's
#: behaviour. A phase branch never carries it: ``phase_branch`` puts a file's
#: behaviour straight after the prefix, so the two names differ for the same
#: behaviour and one group's branch can never be mistaken for a member's.
GROUP_BRANCH_MARKER = "group-"


def _phase_file_for(folder, number):
    """The phase file in ``folder`` whose number matches, or ``None``.

    The match is the one the selector uses: a file's first numeric filename token
    is its phase number. A file with no number and an appendix file are skipped
    by ``phase_file_parts``, so neither can be mistaken for a group member.
    """
    for path in phase_paths(folder):
        parts = phase_file_parts(path)
        if parts and int(parts[0]) == int(number):
            return path
    return None


def group_branch(root, members, roadmap_dir=None):
    """The one branch a reservation group's work lives on.

    A group runs as one unit, so it publishes one branch and one pull request for
    every member. The name is derived from the group's lowest member, which the
    phase documents fix and which therefore stays the same whichever member a
    given tick happens to launch from; deriving it from the member being launched
    would give one group a different branch per member, which is the opposite of
    what the group is for.

    ``members`` are the declared members, in any order, as phase numbers or
    six-digit strings.

    Raises:
        ConfigError: The group is empty, a member is not a phase number, or the
            lowest member has no document in the roadmap folder.
    """
    numbers = set()
    for member in members or ():
        try:
            numbers.add(int(str(member).strip()))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"not a phase number: {member!r}") from exc
    if not numbers:
        raise ConfigError("a group branch needs at least one member")
    folder = roadmap_folder(root, roadmap_dir)
    lowest = min(numbers)
    path = _phase_file_for(folder, lowest)
    if path is None:
        raise ConfigError(
            f"group member {lowest:06d} has no phase document in {folder}; "
            "refusing to guess a branch name"
        )
    _number, behaviour = _phase_file_parts(path.name)
    return f"{PHASE_BRANCH_PREFIX}{GROUP_BRANCH_MARKER}{behaviour}"


def assert_phase_branch_unique(
    root, phase, roadmap_name, roadmap_dir=None, branch=None
):
    """Refuse a branch name another of this checkout's phases also earns.

    The phase number is deliberately absent from the branch name, so the
    behaviour is the only thing keeping two phases apart. A duplicate would make
    the second phase resume the first one's branch and silently inherit its
    commits, so this fails closed and leaves the rename to the operator.

    ``roadmap_dir`` is the folder the phases were chosen from. It defaults to
    the derived one, which is what every machine reads unless it configured
    otherwise.

    ``branch`` is the name to check, and defaults to the name derived from the
    phase's own file. A group passes its own single branch name instead, because
    the group has no one file to derive a name from. A group branch carries the
    group marker, so it can never equal a phase branch's name; a collision
    between two groups is checked separately, by ``assert_group_branch_unique``.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    branch = branch or phase_branch_for(phase, roadmap_name)
    info = {"repo": str(root), "phase": f"{int(phase):06d}", "branch": branch}
    roadmap = roadmap_folder(root, roadmap_dir)
    for path in phase_paths(roadmap):
        if phase_file_parts(path) is None:
            continue
        number, _behaviour = _phase_file_parts(path.name)
        if int(number) == int(phase) or phase_branch(path.name) != branch:
            continue
        return blocked(
            info,
            f"{branch!r} is also the branch for {path.name!r}",
            hint="two phases cannot share a branch; rename one phase file so "
            "the behaviour part differs",
        )
    return dict(info, ok=True, action="phase-branch-name-unique", branch=branch)


def assert_group_branch_unique(root, branch, members, roadmap_dir=None):
    """Refuse a group branch another declared group would also earn.

    Two groups whose lowest member shares a behaviour word derive the same branch
    name, and the second group would resume the first one's commits and publish
    them under one pull request. ``assert_phase_branch_unique`` cannot catch it: a
    group branch carries the group marker and a phase branch does not, so the two
    names never compare equal. The comparison here is group against group, which
    is where the collision actually lives.

    ``members`` are the declared members of the group whose branch is being cut.
    A document whose declaration cannot be read is left to the launch that will
    refuse on it, rather than stopping a branch name this group does not share.

    Returns a result dict; ok is the success flag.
    """
    from argus import roadmap_source

    root = Path(root).resolve()
    wanted = sorted(f"{int(member):06d}" for member in members or ())
    info = {"repo": str(root), "branch": branch, "group": wanted}
    folder = roadmap_folder(root, roadmap_dir)
    for path in phase_paths(folder):
        parts = phase_file_parts(path)
        if parts is None:
            continue
        try:
            other = roadmap_source.declared_group_members(path, int(parts[0]))
        except roadmap_source.ReservationGroupError:
            continue
        if not other:
            continue
        if sorted(f"{int(member):06d}" for member in other) == wanted:
            continue
        if group_branch(root, other, roadmap_dir) != branch:
            continue
        other_members = [f"{int(member):06d}" for member in other]
        return blocked(
            info,
            f"{branch!r} is also the branch for group {other_members!r} "
            f"in {path.name!r}",
            hint="two groups cannot share a branch; rename a member file so the "
            "lowest member's behaviour part differs",
        )
    return dict(info, ok=True, action="group-branch-name-unique")


class ConfigError(Exception):
    """A usage or environment error (exit 2), not a sync conflict."""


def git_binary():
    """The git command named in the loaded config."""
    return setting("commands.git")


def gh_binary():
    """The gh command named in the loaded config."""
    return setting("commands.gh")


def git(repo, *args, timeout=GIT_TIMEOUT_S, extra_env=None, raw=False):
    """Run one git command in repo. Returns (rc, stdout, stderr), stripped
    unless raw=True (porcelain status keeps its leading status column).

    GIT_TERMINAL_PROMPT=0 keeps a missing credential from hanging an
    unattended run on an interactive prompt; a timeout is a failure, not
    a hang."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if extra_env:
        env.update(extra_env)
    try:
        p = subprocess.run(
            [git_binary(), "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ConfigError("git is not on PATH") from exc
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out after {timeout}s"
    if raw:
        return p.returncode, p.stdout, p.stderr
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def is_git_repo(path):
    if path is None:
        return False
    rc, out, _ = git(path, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def resolve_upstream(repo):
    """Return (branch, remote, remote_branch, why). why is None on success;
    otherwise it explains why the checkout cannot be synced safely."""
    rc, branch, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not branch or branch == "HEAD":
        return None, None, None, "detached HEAD or unreadable current branch"
    rc, up, _ = git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if rc != 0 or not up:
        return branch, None, None, f"branch {branch!r} has no upstream"
    remote, _, remote_branch = up.partition("/")
    if not remote or not remote_branch:
        return branch, None, None, f"unreadable upstream {up!r}"
    return branch, remote, remote_branch, None


def remote_urls(repo, remote, *, push=False):
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.extend(["--all", remote])
    rc, out, _ = git(repo, *args)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def canonical_remote_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value and "@" not in value and ":" not in value:
        return str(Path(value).expanduser().resolve())
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme:
        return value
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.query,
            parsed.fragment,
        )
    )


def upstream_urls(repo, *, push=False):
    _branch, remote, _remote_branch, why = resolve_upstream(repo)
    if why or not remote:
        return set()
    return {canonical_remote_url(url) for url in remote_urls(repo, remote, push=push)}


def upstream_push_urls(repo):
    return upstream_urls(repo, push=True)


def classify(repo, upstream):
    """(local, tip, ahead, behind, relation) after a fetch."""
    local = git(repo, "rev-parse", "HEAD")[1]
    tip = git(repo, "rev-parse", upstream)[1]
    ahead = int(git(repo, "rev-list", "--count", f"{upstream}..HEAD")[1] or 0)
    behind = int(git(repo, "rev-list", "--count", f"HEAD..{upstream}")[1] or 0)
    if ahead == 0 and behind == 0:
        relation = "equal"
    elif ahead == 0:
        relation = "behind"
    elif behind == 0:
        relation = "ahead"
    else:
        relation = "diverged"
    return local, tip, ahead, behind, relation


def subjects(repo, rev_range):
    rc, out, _ = git(
        repo, "log", "--oneline", "--no-decorate", f"-n{MAX_SUBJECTS}", rev_range
    )
    return out.splitlines() if rc == 0 and out else []


def incoming_paths(repo, upstream):
    """The paths the incoming commits change, as a conservative superset.

    The comparison is against the merge base, not against HEAD, because that is
    the set the remote side actually brings: ``diff HEAD upstream`` would list
    every path the local side has and the remote side does not as a deletion,
    so a diverged phase would block on its own new files.

    ``--no-renames`` lists a rename as a delete plus an add, so an edit to
    either side of a rename is caught. Over-refusing is the safe direction: the
    cost of a false overlap is one halted tick, the cost of a missed one is a
    merge over unreviewed work.
    """
    rc, base, _ = git(repo, "merge-base", "HEAD", upstream)
    if rc != 0 or not base:
        return []
    rc, out, _ = git(repo, "diff", "--name-only", "--no-renames", base, upstream)
    return sorted(out.splitlines()) if rc == 0 and out else []


def overlapping_paths(repo, upstream, local):
    """The locally-dirty paths the incoming commits also change."""
    incoming = set(incoming_paths(repo, upstream))
    return sorted(incoming & set(local))


def blocked(info, reason, **extra):
    info = dict(info)
    info["ok"] = False
    info["action"] = "blocked"
    blockers = list(info.get("blockers", []))
    blockers.append(reason)
    info["blockers"] = blockers
    info.update(extra)
    return info


def _fast_forward(info, repo, upstream, behind):
    """Integrate a strictly-behind remote by fast-forward only (no merge
    commit).

    A phase leaves the index dirty for its whole life, so a dirty index on its
    own is not a reason to stop. A fast-forward writes the incoming tree
    straight over the working tree, so git preserves a dirty index whose paths
    the incoming commits do not touch; the only thing that can go wrong is a
    path both want. Refuse that, name it, and do not ask git to try.
    """
    local = dirty_paths(repo)
    clash = overlapping_paths(repo, upstream, local)
    if clash:
        return blocked(
            info,
            "incoming commits and local work touch the same paths",
            overlapping=clash,
            dirty=sorted(local),
            hint="commit or move the overlapping edits, then rerun; the "
            "pipeline will not merge over unreviewed work",
        )
    rc, out, err = git(repo, "merge", "--ff-only", upstream)
    if rc != 0:
        return blocked(
            info,
            f"fast-forward merge refused: {err or out}",
            dirty=sorted(local),
            overlapping=[],
            hint="git refused for a reason the overlap check does not name; "
            "read the message and resolve it by hand",
        )
    after = git(repo, "rev-parse", "HEAD")[1]
    return dict(
        info,
        ok=True,
        action="fast-forwarded",
        after=after,
        carried=sorted(local),
        hint=f"advanced {behind} commit(s) to {upstream}, carrying "
        f"{len(local)} uncommitted path(s) over",
    )


def _merge(info, repo, upstream, ahead, behind, abort_on_conflict=True):
    """Integrate diverged histories with a plain merge commit: accept the
    remote changes while keeping every local commit. Never rebases or
    forces. On a real conflict, start mode aborts and halts; push mode
    leaves the conflict in place so the finalize agent can resolve it.

    A real merge has a stricter precondition than a fast-forward, and it is
    git's rather than ours: git refuses to merge while the index differs from
    HEAD, disjoint or not, and does so under every merge strategy. So the index
    stays a hard gate here. Unstaged and untracked work is carried through, but
    only when the incoming commits do not touch it.
    """
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        return blocked(
            info,
            "staged changes present; a merge of diverged histories "
            "needs the index to match HEAD",
            staged=staged.splitlines(),
            hint="commit or unstage the phase work, then rerun; git refuses a "
            "merge with a dirty index even when the paths are disjoint",
        )
    local = dirty_paths(repo)
    clash = overlapping_paths(repo, upstream, local)
    if clash:
        return blocked(
            info,
            "incoming commits and local work touch the same paths",
            overlapping=clash,
            dirty=sorted(local),
            hint="commit or move the overlapping edits, then rerun; the "
            "pipeline will not merge over unreviewed work",
        )
    rc, out, err = git(
        repo, "merge", "--no-edit", upstream, extra_env={"GIT_MERGE_AUTOEDIT": "no"}
    )
    if rc != 0:
        _, conflicts, _ = git(repo, "diff", "--name-only", "--diff-filter=U")
        aborted = None
        if abort_on_conflict:
            _, _, ab_err = git(repo, "merge", "--abort")
            aborted = not ab_err
        reason = "clean merge impossible; remote changes conflict with local work"
        hint = "resolve by hand; the pipeline will not rebase or force"
        if not abort_on_conflict:
            hint = (
                "resolve the conflicted files, `git add` them, and "
                "complete the merge with `git commit`; never force or "
                "rebase"
            )
        return blocked(
            info,
            reason,
            conflicted=conflicts.splitlines() if conflicts else [],
            merge_error=(err or out),
            merge_aborted=aborted,
            hint=hint,
        )
    after = git(repo, "rev-parse", "HEAD")[1]
    return dict(
        info,
        ok=True,
        action="merged",
        after=after,
        carried=sorted(local),
        hint=f"merged {behind} remote commit(s) with {ahead} local "
        f"commit(s); push will fast-forward",
    )


def sync_one(repo, mode, remote=None, branch=None):
    """Bring one checkout in line with its remote, accepting changes where
    that is mechanically safe.

    Fast-forwards a strictly-behind branch; merges (never rebases) a
    diverged branch and keeps every local commit. In start mode a real
    conflict, a dirty index, or a detached HEAD halts with the merge
    aborted. In push mode an ahead branch is verified as a fast-forward
    push, and a conflict is left in place for the finalize agent to
    resolve. Returns a result dict; ok is the success flag."""
    repo = Path(repo).resolve()
    info = {"repo": str(repo), "mode": mode}
    if not is_git_repo(repo):
        return blocked(info, "not a git working tree")
    cur_branch, cur_remote, remote_branch, why = resolve_upstream(repo)
    if why:
        return blocked(info, why)
    branch = branch or cur_branch
    remote = remote or cur_remote
    if branch != cur_branch:
        return blocked(info, f"checkout is on {cur_branch!r}, not {branch!r}")
    info.update(branch=branch, remote=remote)
    rc, _, err = git(repo, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return blocked(info, f"git fetch {remote} failed", detail=err)
    upstream = f"{remote}/{remote_branch}"
    rc, tip, _ = git(repo, "rev-parse", "--verify", upstream)
    if rc != 0 or not tip:
        return blocked(info, f"upstream {upstream} does not exist after fetch")
    local, tip, ahead, behind, relation = classify(repo, upstream)
    info.update(
        upstream=upstream,
        before=local,
        remote_tip=tip,
        ahead=ahead,
        behind=behind,
        relation=relation,
        local_only=subjects(repo, f"{upstream}..HEAD"),
        remote_only=subjects(repo, f"HEAD..{upstream}"),
    )
    if relation == "equal":
        return dict(info, ok=True, action="up-to-date", hint="local and remote match")
    if relation == "ahead":
        if mode == "push":
            return dict(
                info,
                ok=True,
                action="verified",
                hint="remote is an ancestor of local HEAD; push will fast-forward",
            )
        return dict(
            info,
            ok=True,
            action="ahead",
            hint=f"remote has nothing new; {ahead} local commit(s) "
            f"will publish at finalize",
        )
    if relation == "behind":
        return _fast_forward(info, repo, upstream, behind)
    return _merge(
        info, repo, upstream, ahead, behind, abort_on_conflict=(mode != "push")
    )


def resync_integration(repo, base=None):
    """Merge the integration branch into the checkout's current phase branch.

    The gap this closes: nothing pulls the integration branch into a running
    phase. The pre-launch sync brings the *current* branch in line with its own
    remote, and the publish-time sync compares the phase branch against its own
    upstream, so a master that moves during the phase is invisible until a
    reviewer sees a stale diff.

    It reuses the two integration primitives rather than adding a third. The
    classification is the same ``classify`` the normal sync uses, so a phase
    behind the integration branch fast-forwards and a diverged one takes a
    plain merge commit, never a rebase.

    The conflict is left in place, not aborted, and that is the whole point: a
    conflict the pipeline cleans up is a conflict nobody fixes, and a phase that
    cannot resolve one cannot be handed back to the agent either. The finalize
    stage already has the instruction to finish such a merge by hand, and
    ``01-implement.md`` gains the same one.

    A checkout already on the integration branch is a no-op, which is what the
    private checkout always is.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info = {"repo": str(repo), "mode": "resync", "base": base}
    if not is_git_repo(repo):
        return blocked(info, "not a git working tree")
    here = current_branch(repo)
    if not here:
        return blocked(
            info,
            "detached HEAD; refusing to move the working tree",
            hint="check out the phase branch, then rerun",
        )
    info["branch"] = here
    if here == base:
        return dict(
            info,
            ok=True,
            action="already-on-integration-branch",
            hint=f"already on {base}; there is no phase branch to resync",
        )
    # The remote is read from the phase branch first, and from the base branch
    # second, because a phase branch that has never been pushed has no upstream
    # of its own and the sync helper refuses a branch with no remote copy.
    _branch, remote, _rb, why = resolve_upstream(repo)
    if not remote:
        rc_r, from_base, _ = git(repo, "config", "--get", f"branch.{base}.remote")
        remote = from_base if rc_r == 0 else None
    if not remote:
        return blocked(
            info,
            why or "no remote is configured",
            hint="the integration branch cannot be merged in "
            "without a remote to read it from",
        )
    rc, _, err = git(repo, "fetch", "--prune", remote, timeout=FETCH_TIMEOUT_S)
    if rc != 0:
        return blocked(info, f"git fetch {remote} failed", detail=err)
    upstream = f"{remote}/{base}"
    rc, _, _ = git(repo, "rev-parse", "--verify", upstream)
    if rc != 0:
        return blocked(
            info,
            f"{upstream} does not exist after fetch",
            hint="the integration branch is missing on the remote",
        )
    local, tip, ahead, behind, relation = classify(repo, upstream)
    info.update(
        upstream=upstream,
        before=local,
        remote_tip=tip,
        ahead=ahead,
        behind=behind,
        relation=relation,
    )
    if relation == "equal":
        return dict(
            info,
            ok=True,
            action="up-to-date",
            hint=f"{here} already contains {upstream}",
        )
    if relation == "behind":
        return _fast_forward(info, repo, upstream, behind)
    if relation == "ahead":
        # The phase branch has commits the integration branch does not, and the
        # integration branch has nothing new. Merging would be a no-op with a
        # commit attached, so it is reported and left alone.
        return dict(
            info,
            ok=True,
            action="phase-ahead",
            hint=f"{base} has nothing new; {ahead} phase commit(s) "
            "stay local until finalize pushes them",
        )
    # A diverged phase branch is the only case that needs a real merge, and a
    # real merge is the only case git refuses while the index is dirty. That is
    # reported under its own action with the staged paths, because "the merge
    # could not happen" and "the merge would have conflicted" need different
    # responses from whoever reads it, and _merge's single blocked result
    # collapses them into one line that reads the same either way.
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        blocked_result = blocked(
            info,
            "the index holds staged changes, and git refuses to merge "
            "diverged histories while the index differs from HEAD",
            staged=staged.splitlines(),
            hint="run this again once the index is clean — after committing, "
            "or from a tree with nothing staged. This is git's rule, not "
            "this helper's, so there is no flag that bypasses it",
        )
        # blocked() hardcodes action="blocked"; the distinct action is the whole
        # point, because the driver and the run log both branch on it.
        blocked_result["action"] = "resync-blocked-dirty-index"
        return blocked_result
    return _merge(info, repo, upstream, ahead, behind, abort_on_conflict=False)


def sync_all(root, mode, branch=None):
    """Sync the public checkout and the nested private checkout, if present.

    ``branch`` is the branch the public checkout must be on, and it reaches that
    checkout alone: the private one is read on the integration branch, so naming
    a group branch for it would refuse it for no reason. A caller that names no
    branch leaves the check comparing a checkout's own branch against itself,
    which is what every caller did before a group could name a branch at all.
    """
    root = Path(root).resolve()
    repos = [root]
    nested = board_repo(root)
    if nested is not None and is_git_repo(nested):
        repos.append(nested)
    if len(repos) == 2:
        public_fetch = upstream_urls(root)
        public_push = upstream_push_urls(root)
        private_fetch = upstream_urls(nested)
        private_push = upstream_push_urls(nested)
        if not public_fetch or not public_push or not private_fetch or not private_push:
            reason = (
                "cannot prove separate public/private Git destinations; "
                "refusing to sync either checkout"
            )
            results = [blocked({"repo": str(r), "mode": mode}, reason) for r in repos]
            return {
                "ok": False,
                "mode": mode,
                "repos": results,
                "summary": summarize(results, mode),
            }
        if (
            public_push & private_push
            or public_push & private_fetch
            or public_fetch & private_push
        ):
            reason = (
                "nested private repo and public repo share a Git "
                "push/fetch destination; refusing to sync either checkout"
            )
            results = [blocked({"repo": str(r), "mode": mode}, reason) for r in repos]
            return {
                "ok": False,
                "mode": mode,
                "repos": results,
                "summary": summarize(results, mode),
            }
    results = [sync_one(repos[0], mode, branch=branch)]
    results += [sync_one(r, mode) for r in repos[1:]]
    ok = all(r["ok"] for r in results)
    return {
        "ok": ok,
        "mode": mode,
        "repos": results,
        "summary": summarize(results, mode),
    }


def dirty_paths(repo):
    """Repo-relative paths with any staged, unstaged, or untracked change.

    Ignored files (for example the runner's interface log and driver state) do not
    appear, so this is exactly the set a commit would carry."""
    rc, out, _ = git(
        repo, "status", "--porcelain=v1", "--untracked-files=all", raw=True
    )
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:  # renames: "XY old -> new"
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def _is_private_path(root, path):
    """Exclude only the configured nested private checkout from public dirt."""
    private = board_repo(root)
    if private is None:
        return False
    try:
        prefix = str(private.relative_to(Path(root).resolve()))
    except ValueError:
        return False
    return path == prefix or path.startswith(prefix + "/")


def is_run_state(path, scope="runs"):
    """True for the pipeline's own run-state paths inside the private repo."""
    return path == scope or path.startswith(scope.rstrip("/") + "/")


def current_branch(repo):
    """The checked-out branch, or an empty string on a detached HEAD."""
    rc, out, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out if rc == 0 and out and out != "HEAD" else ""


def ensure_phase_branch(repo, branch, base=None, integration_only=False):
    """Put a checkout on its phase branch, creating it from ``base`` if needed.

    Idempotent, because a driver tick runs more than once and a phase that is
    already on its branch must not be moved. A phase that is on some *other*
    branch is a hard stop rather than a silent switch: switching branches under
    a working tree that holds unreviewed work is exactly the operation that can
    leave that work in pieces, and the operator is the only one who can decide
    it is safe.

    The integration branch is the one exception, and only with a clean tree.
    It is the ordinary state between two phases: the workflow asks a person to
    return the checkout there once a pull request is open, and the tidy step
    does it once a phase is published. When the next phase stacks on an earlier
    phase whose pull request is still open, its base is that earlier phase's
    branch and not the integration branch, so a checkout sitting on the
    integration branch is a starting point and not a conflict. Refusing it meant
    every such launch needed a person to run a checkout first, and the line
    stopped on each one in turn. A clean tree is the whole condition: nothing
    can be reinterpreted against the other commit. Uncommitted work on the
    integration branch is still a stop, and the message names the paths.

    The index is carried across, not required to be clean, when the checkout is
    already on the base. A phase stages its work and never commits it, so the
    branch is created from the same commit the working tree is already on and
    the staged content does not move.

    ``integration_only`` marks a checkout whose contents are reviewed on the
    integration branch rather than as a pull request, which is the private
    repository. Such a checkout is left on the integration branch and reported
    as ``stays-on-integration-branch``, so the caller can still tell the two
    apart without either one being a silent no-op.

    Every outcome carries ``after_branch``, the branch the checkout is actually
    on once this returns. The caller logs it, and a log line built from the
    requested ``branch`` instead reads the same for both checkouts, so a pair
    that was branched the wrong way round still looked like a correct launch.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info = {"repo": str(repo), "branch": branch, "base": base}
    if not is_git_repo(repo):
        return blocked(info, "not a git working tree")
    # Recorded up front so every return below, blocked ones included, reports
    # where the checkout really is rather than only what was asked of it.
    info["after_branch"] = current_branch(repo) or "(detached)"
    if integration_only:
        here = current_branch(repo)
        rc, _, err = git(repo, "rev-parse", "--verify", base)
        if rc != 0:
            return blocked(
                info,
                f"base branch {base!r} does not exist",
                hint="the integration branch is missing; refusing to "
                "publish run state with no starting point",
            )
        if here and here != base:
            return blocked(
                info,
                f"checkout is on {here!r}, not {base!r}",
                hint="run state is published from the integration branch; "
                "moving the working tree is an operator decision",
            )
        return dict(
            info,
            ok=True,
            action="stays-on-integration-branch",
            after=git(repo, "rev-parse", "HEAD")[1],
            after_branch=base,
            hint=f"left on {base}; this checkout's contents are read "
            f"on the integration branch, not as a pull request",
        )
    here = current_branch(repo)
    if here == branch:
        return dict(
            info,
            ok=True,
            action="already-on-phase-branch",
            after_branch=branch,
            hint="the phase is already on its own branch",
        )
    # The remote is read from the base branch, before anything is switched: a
    # branch that does not exist yet has no upstream of its own, so asking this
    # checkout would answer about the wrong branch.
    _base_ref, remote, _rb, why = resolve_upstream(repo)
    if why or not remote:
        rc_r, remote, _ = git(repo, "config", "--get", f"branch.{base}.remote")
        if rc_r != 0 or not remote:
            return blocked(
                info,
                why or "no remote is configured",
                hint="the phase branch cannot be published; "
                "refusing to leave it with no remote copy",
            )
    # The base is checked before the current branch, so a missing integration
    # branch is reported as itself rather than as "you are on the wrong
    # branch", which is what the reverse order would say.
    rc, _, err = git(repo, "rev-parse", "--verify", base)
    if rc != 0:
        return blocked(
            info,
            f"base branch {base!r} does not exist",
            hint="the integration branch is missing; refusing to "
            "invent a starting point",
        )
    if here and here != base:
        # The integration branch is where the checkout is left between phases,
        # so it is a place the launch may leave from rather than a conflict it
        # must refuse. The tree has to be clean for that: the phase branch is
        # cut from the base's commit, so anything uncommitted would be
        # reinterpreted against a tree it was never written for. That is the
        # case the refusal below exists for, and it keeps refusing it.
        if here == integration_branch(repo):
            carried = dirty_paths(repo)
            if carried:
                return blocked(
                    info,
                    f"checkout is on {here!r} with {len(carried)} "
                    f"uncommitted path(s), and the phase branch is cut from "
                    f"{base!r}",
                    dirty=carried,
                    hint="commit or stash the work, or move the checkout to the "
                    f"base branch {base!r} yourself, then rerun; nothing "
                    "was switched",
                )
        else:
            return blocked(
                info,
                f"checkout is on {here!r}, not the phase branch or {base!r}",
                hint="a phase's work lives on its own branch; moving it is an "
                "operator decision, so nothing was switched",
            )
    exists_rc, _, _ = git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    if exists_rc == 0:
        # The branch is left over from an earlier run of this same phase. Its
        # commits are the phase's own history, so move onto it rather than
        # creating a second one; the overlap gate decides whether the two tips
        # can be joined.
        rc, out, err = git(repo, "checkout", branch)
        if rc != 0:
            return blocked(
                info,
                f"could not check out {branch!r}: {err or out}",
                hint="resolve by hand; nothing was switched",
            )
        return dict(
            info,
            ok=True,
            action="reused-phase-branch",
            after=git(repo, "rev-parse", "HEAD")[1],
            after_branch=branch,
            hint=f"resumed the existing {branch}",
        )
    rc, out, err = git(repo, "checkout", "-b", branch, base)
    if rc != 0:
        return blocked(
            info,
            f"could not create {branch!r}: {err or out}",
            hint="resolve by hand; nothing was switched",
        )
    # Publish the new branch and give it an upstream straight away. Two
    # reasons, both load-bearing: the sync helper refuses a branch with no
    # upstream, and a branch with no remote copy is the state the phase branch
    # exists to end. The push is at the same commit as the integration branch,
    # so it publishes no unreviewed work.
    rc, out, err = git(repo, "push", "-q", "-u", remote, branch)
    if rc != 0:
        return blocked(
            info,
            f"could not publish {branch!r} to {remote}: {err or out}",
            hint="the phase branch must exist on the remote before the phase "
            "starts; resolve by hand",
        )
    return dict(
        info,
        ok=True,
        action="created-phase-branch",
        after=git(repo, "rev-parse", "HEAD")[1],
        after_branch=branch,
        carried=dirty_paths(repo),
        hint=f"created {branch} from {base} and published it, "
        f"carrying the phase's uncommitted work",
    )


def branch_phase_checkouts(root, branch, base=None):
    """Put the public checkout on ``branch`` and leave the private one alone.

    The two checkouts are reviewed in two different ways, so they are branched
    in two different ways. The public checkout holds a phase's product code,
    which is reviewed as a pull request, so it gets a real phase branch. The
    private checkout holds run state and phase notes, which the operator reads
    on the integration branch, so it is left there and published by pushing
    that branch.

    The private checkout is the one that stays. It is named here rather than
    inlined at the call site because the two are easy to swap and the swap is
    silent: both checkouts report success either way, so a launch onto the
    integration branch looks exactly like a correct one until a phase writes
    unreviewed code straight onto it.

    ``base`` is the commit the phase branch starts at. It defaults to the
    integration branch, and the caller passes the open predecessor's branch
    instead when there is one, so the phase is built on the code the previous
    phase wrote. It applies to the public checkout only.

    The private checkout never gets it. That checkout has no phase branches at
    all: run state is read by the operator on the integration branch and
    published by pushing it, so a predecessor's branch is not a branch it can
    ever sit on. Passing one would fail its own ``here != base`` check and stop
    every launch, which is the kind of failure that reads as a broken pipeline
    rather than as a wiring mistake.

    Returns one result dict per checkout, public first.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    targets = [root] + ([private] if is_git_repo(private) else [])
    return [
        ensure_phase_branch(
            r,
            branch,
            base=(None if r == private else base),
            integration_only=(r == private),
        )
        for r in targets
    ]


# Run artifacts the running engine rewrites in place while a step runs.
#
# The test is the property, not the filename: a file sitting directly in a
# phase's run directory whose whole content is a timestamp or an engine audit
# line. The engine that is publishing is the engine that writes them, at least
# once a second, so a check that demands a clean tree races the writer it is
# checking and cannot win. The publication helper used to be unpublishable
# from inside the engine for exactly that reason.
#
# These files are still committed and still pushed. The exemption is only
# about the *check* that runs after the commit, and it is deliberately narrow:
# a run log, findings, a ledger, a resume record, a receipt, a modified source
# file, a staged change and an untracked file all still refuse publication.
# Both halves of the test matter: the name alone would exempt a source file
# that happens to be called `activity`, and the directory alone would exempt
# every run artifact. Adding a name here is a commitment to say why the
# artifact is engine telemetry and not phase evidence, so the next hot
# artifact cannot slip in silently.
ENGINE_WRITTEN_RUN_ARTIFACTS = (
    # glide.nudge.touch_activity: "harness output at <epoch>", rewritten on
    # every pump tick where the harness produced output.
    "activity",
    # glide.nudge.record: the reminder audit, appended on every decision.
    "reminders.log",
)

# A run artifact is a file directly inside runs/phase-<N>/. The path may carry
# a repository prefix, because the publish check reports the private checkout's
# paths as seen from the public one.
_RUN_ARTIFACT_RE = re.compile(r"(?:^|/)runs/(phase-\d+)/([^/]+)$")


def is_engine_written_run_artifact(path):
    """Whether a repo-relative path is engine telemetry the running step
    rewrites in place, rather than phase evidence.

    Args:
        path: A repo-relative path, as :func:`dirty_paths` reports it.

    Returns:
        Whether the path is an engine-written artifact of one phase.
    """
    match = _RUN_ARTIFACT_RE.search(path)
    return bool(match) and match.group(2) in ENGINE_WRITTEN_RUN_ARTIFACTS


def publish_dirt(dirty, root=None, phase=None):
    """Split dirty paths into the ones that must refuse publication and the
    engine-written ones that were already committed and pushed.

    The excluded set is reported, never dropped: an operator reading a
    publication can see what the check chose not to look at.
    """
    if root is not None and phase is not None:
        run = phase_run_dir(root, phase).resolve()

        def engine_artifact(path):
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(root) / candidate
            return (
                candidate.resolve().parent == run
                and candidate.name in ENGINE_WRITTEN_RUN_ARTIFACTS
            )
    else:
        engine_artifact = is_engine_written_run_artifact
    real = [p for p in dirty if not engine_artifact(p)]
    engine = sorted(p for p in dirty if engine_artifact(p))
    return real, engine


def _commit_and_push_run_state(repo, message, pathspec="runs"):
    """Commit one scoped run-state path and push without force.

    ``pathspec`` is deliberately narrow. A phase must never publish another
    phase's run directory merely because both are dirty at the same time.
    """
    branch, remote, remote_branch, why = resolve_upstream(repo)
    info = {"repo": str(repo), "pathspec": pathspec}
    if why:
        return blocked(info, why)
    _, staged, _ = git(repo, "diff", "--cached", "--name-only")
    if staged:
        prefix = pathspec.rstrip("/") + "/"
        outside = [
            p for p in staged.splitlines() if p != pathspec and not p.startswith(prefix)
        ]
        if outside:
            return blocked(
                info,
                "staged changes exist outside the current phase",
                staged=outside,
                hint="commit or unstage them before publishing",
            )
    rc, _, err = git(repo, "add", "-A", "--", pathspec)
    if rc != 0:
        return blocked(info, f"could not stage {pathspec}: {err}")
    rc, out, err = git(repo, "commit", "-q", "-m", message)
    if rc != 0:
        combined = err or out
        if "nothing to commit" in combined.lower():
            return dict(info, ok=True, action="clean", hint=message)
        return blocked(info, f"commit of run state failed: {combined}")
    for attempt in (1, 2):
        rc, out, err = git(repo, "push", remote, branch)
        if rc == 0:
            after = git(repo, "rev-parse", "HEAD")[1]
            return dict(
                info, ok=True, action="committed-and-pushed", after=after, hint=message
            )
        if attempt == 1:
            res = sync_one(repo, "start")
            if not res.get("ok"):
                return blocked(
                    info,
                    "remote moved and could not be integrated before pushing run state",
                    detail=res.get("summary"),
                )
    return blocked(
        info,
        f"push of run state failed: {err or out}",
        hint="resolve the remote by hand, then rerun",
    )


def _phase_run_pathspec(phase, root=None):
    root = Path(root or Path.cwd()).resolve()
    try:
        number = int(phase)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid phase number: {phase!r}") from None
    if number < 0 or number > 999999:
        raise ConfigError(f"phase number out of range: {number}")
    private = board_repo(root)
    if private is None:
        raise ConfigError("the reservation board repository is not configured")
    try:
        resolved = phase_run_dir(root, number).resolve()
    except ValueError as exc:
        # The run directory could not be computed at all, most often because the
        # phase file the pipeline names is gone. This is a different failure from
        # a directory that lands outside the private checkout, and reporting it
        # as one sent a reader looking at paths when the input was missing.
        raise ConfigError(
            f"cannot locate the run directory for phase {number}: {exc}"
        ) from exc
    try:
        return str(resolved.relative_to(private.resolve()))
    except ValueError as exc:
        raise ConfigError(
            "phase run directory must be inside the private checkout to publish"
        ) from exc


def commit_leftover_run_state(root, message, phase=None):
    """Commit only the current phase's private run state.

    With ``phase`` omitted, the legacy all-``runs`` scope remains available
    for standalone maintenance. The pipeline always supplies a phase so a
    concurrent phase's partial records cannot be swept into this publication.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    pathspec = (
        _phase_run_pathspec(phase, root)
        if phase is not None
        else str(runs_dir(root).relative_to(private))
    )
    results = []
    # The nested private checkout is a separate repo by design; if a host
    # lacks the global ignore for it, do not mistake it for public dirt.
    pub_dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
    if pub_dirty:
        results.append(
            blocked(
                {"repo": str(root)},
                "public checkout has changes outside the pipeline's run state",
                dirty=pub_dirty,
                hint="commit or revert them, then rerun",
            )
        )
    if is_git_repo(private):
        paths = dirty_paths(private)
        if phase is None:
            outside = [p for p in paths if not is_run_state(p, pathspec)]
            inside = [p for p in paths if is_run_state(p, pathspec)]
        else:
            prefix = pathspec + "/"
            # Unstaged files from another phase are deliberately left alone;
            # only staged paths outside this phase are dangerous because a
            # commit would carry them accidentally.
            _, staged, _ = git(private, "diff", "--cached", "--name-only")
            outside = [
                p
                for p in staged.splitlines()
                if p != pathspec and not p.startswith(prefix)
            ]
            inside = [p for p in paths if p == pathspec or p.startswith(prefix)]
        if outside:
            results.append(
                blocked(
                    {"repo": str(private)},
                    "private checkout has changes outside the current phase"
                    if phase is not None
                    else "private checkout has changes outside runs/",
                    dirty=outside,
                    hint="commit or move them before publishing this phase",
                )
            )
        elif inside:
            results.append(
                _commit_and_push_run_state(private, message, pathspec=pathspec)
            )
        else:
            results.append({"repo": str(private), "ok": True, "action": "clean"})
    ok = all(r.get("ok") for r in results)
    return {
        "ok": ok,
        "mode": "start-clean",
        "repos": results,
        "pathspec": pathspec,
        "summary": summarize(results, "start-clean"),
    }


def _published_head(results, repo):
    for result in results:
        if result.get("repo") == str(repo) and result.get("action") == "pushed":
            return result.get("after")
    return None


def phase_receipt(repo, number):
    """A phase's publication receipt, or None when there is none.

    A receipt is the only record of where a phase's commits ended up, so it is
    read by the selector to decide whether the phase is still waiting for a
    human. A receipt that does not parse is treated as absent, not as
    completion: an unreadable receipt must never read as a merged phase.
    """
    path = phase_run_dir(repo, number) / "publication.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def head_tree_on_integration(checkout, head, integration):
    """Whether ``head``'s file content is already on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see it. What survives is the content: the squash commit's tree is
    the phase branch's tree, so the phase's work is present on the integration
    branch under a different commit id.

    This is the proof that still works when GitHub cannot be asked at all, which
    is the case that matters most — a missing or unauthenticated ``gh`` must
    degrade to a working answer, not to a line that never starts again.

    Returns True or False. False means "not found", which is the safe answer:
    the caller then falls back to the branch itself, so a phase that really was
    merged is never mistaken for one that was not.
    """
    rc, tree, _ = git(checkout, "rev-parse", "--verify", f"{head}^{{tree}}")
    if rc != 0 or not tree:
        return False
    rc, _, _ = git(checkout, "cat-file", "-e", f"{integration}^{{tree}}")
    if rc != 0:
        return False
    # The integration branch's own tip, checked first because it is the common
    # case and costs one comparison rather than a walk.
    rc, tip_tree, _ = git(checkout, "rev-parse", "--verify", f"{integration}^{{tree}}")
    if rc == 0 and tip_tree == tree:
        return True
    # Otherwise the squash commit's tree is somewhere in the branch's history.
    rc, found, _ = git(checkout, "log", "--format=%T", "-n", "200", integration)
    return rc == 0 and tree in found.split()


def phase_work_on_integration(root, phase, receipt=None):
    """Whether this phase's code is already on the integration branch.

    One derived fact, asked one way, because two places need this answer and
    they were asking it differently: the merge check decided it from the
    receipt's recorded state, and the base-branch chooser decided it from the
    same receipt. A phase whose pull request a human had already merged left the
    receipt stale, so both places kept believing the phase was open — and the
    next phase was cut from a branch whose content was already on master, which
    the branch guard then refused because the checkout was on master. The line
    retried that every tick and never recovered.

    The answer, in order:

    1. A receipt already saying ``published`` is settled and needs no work.
    2. GitHub, asked whether the pull request is ``MERGED``. The reference is
       parsed from the receipt's URL when the receipt predates the recorded
       reference, so an old receipt is still askable.
    3. The commit-id test, for a merge performed outside GitHub.
    4. The content test, for a squash merge seen without GitHub.

    Step 4 is a *code-is-already-there* proof, not a *this-pull-request-was-
    merged* proof, and the difference matters. It may be satisfied by a change
    that reached the integration branch by some other route. That is why it is
    safe for choosing a base branch — where the only question is which tree
    already holds the code — and why ``mark_phase_published`` does not use it.
    A receipt records that a human merged a specific pull request, and only
    GitHub or that pull request's own commit may say so.

    Returns a dict with ``on_integration`` (bool), ``confirmed_by`` and
    ``reason``.
    """
    root = Path(root).resolve()
    info = {
        "phase": f"{int(phase):06d}",
        "on_integration": False,
        "confirmed_by": None,
        "reason": "no publication receipt",
    }
    receipt = receipt if receipt is not None else phase_receipt(root, phase)
    if not receipt:
        return info
    if receipt.get("state") == "published":
        info.update(
            on_integration=True,
            confirmed_by="receipt",
            reason="the receipt already records the merge",
        )
        return info
    integration = receipt.get("integration_branch") or integration_branch(root)
    head = receipt.get("public_head")
    # The parsed reference is used when the receipt has one, and derived from the
    # URL when it does not. That second path is the fix for a receipt written
    # before the reference was recorded: it names the pull request in plain
    # sight, and the old code simply never asked about it.
    ref = receipt.get("pull_request_ref") or {}
    if not isinstance(ref, dict) or not ref.get("number"):
        ref = pull_request_ref(receipt.get("pull_request")) or {}
    github = {"state": "not-asked", "detail": "no pull request reference"}
    if ref.get("number"):
        state, detail = gh_pull_request_state(
            ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
        )
        github = {"state": state, "detail": detail}
        if state == "merged":
            info.update(
                on_integration=True,
                confirmed_by="github",
                reason=f"GitHub reports pull request {ref['number']} merged",
            )
            info["github"] = github
            return info
    info["github"] = github
    if head and is_git_repo(root):
        if _head_carried_locally(root, head, (integration,)):
            info.update(
                on_integration=True,
                confirmed_by="commit-id",
                reason=f"the published head {head[:12]} is on {integration}",
            )
            return info
        if head_tree_on_integration(root, head, integration):
            info.update(
                on_integration=True,
                confirmed_by="content",
                reason=f"the published content {head[:12]} is on "
                f"{integration} under another commit",
            )
            return info
    info["reason"] = "nothing confirms the phase was merged"
    return info


def awaiting_review(repo, number):
    """The branch and pull request a phase is waiting on, or None.

    A phase is waiting for a human when its receipt says so. The receipt is
    written by finalize, which pushes the branch and opens the pull request but
    never merges it, so the phase is not done and the line waits.

    This reads the receipt's recorded state and nothing else. It is what the
    driver builds its merge-check list from, so it must keep reporting a phase
    whose receipt still says ``awaiting_review`` even when the code is already
    on master: that is precisely the receipt the merge check exists to repair,
    and a repaired receipt needs GitHub or its own commit, not an inference
    about content. ``open_predecessor`` is where the content proof belongs.
    """
    receipt = phase_receipt(repo, number)
    if not receipt or receipt.get("state") != "awaiting_review":
        return None
    return {
        "branch": receipt.get("branch"),
        "pull_request": receipt.get("pull_request"),
        "head": receipt.get("public_head"),
    }


def _run_phase_number(entry):
    parts = phase_file_parts(entry.name + ".md")
    if parts is not None:
        return int(parts[0])
    match = re.fullmatch(r"phase-(\d+)", entry.name)
    if match:
        return int(match.group(1))
    try:
        receipt = json.loads((entry / "publication.json").read_text())
        return int(receipt["phase"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _phase_run_entries(root):
    if setting("pipeline", None) is not None:
        roadmap = roadmap_folder(root)
        for phase_file in phase_paths(roadmap):
            parts = phase_file_parts(phase_file)
            if parts is not None:
                entry = phase_run_dir(root, parts[0], phase_file)
                if entry.is_dir():
                    yield parts[0], entry
        return
    try:
        entries = sorted(runs_dir(root).iterdir())
    except OSError:
        return
    for entry in entries:
        number = _run_phase_number(entry)
        if number is not None and entry.is_dir():
            yield number, entry


def open_predecessor(root, phase):
    """The highest earlier phase whose code is not yet on master, or None.

    A phase that is waiting for review is finished work. The next phase must be
    built on top of it, not beside it, or the next phase's agent works from a
    tree that does not contain the code it depends on and fails on a symbol that
    does not exist. The earlier phase is the right base because it is the branch
    carrying that code.

    A phase whose code is *already on* the integration branch is not a
    predecessor, whatever its receipt still says. This is where the content
    proof is allowed, because the question here is only which tree already
    carries the code — and stacking the next phase on a branch whose content is
    already on master is what made the branch guard refuse, on every tick, for
    ever. The human merged the pull request; the receipt was never updated; and
    the line had no way to notice.

    Scanning the run directories rather than the roadmap is deliberate. The
    roadmap lists every phase the operator has written, including ones that have
    never run; only a receipt proves a phase reached review. A phase whose
    receipt is unreadable is skipped, matching ``phase_receipt``, because an
    unreadable receipt must never be treated as a base branch.

    Returns ``{"phase", "branch", "pull_request", "head"}`` or None.
    """
    root = Path(root).resolve()
    best = None
    for number, _entry in _phase_run_entries(root):
        if number >= int(phase):
            continue
        review = awaiting_review(root, number)
        if not review or not review.get("branch"):
            continue
        if phase_work_on_integration(root, number)["on_integration"]:
            continue
        if best is None or number > best["phase"]:
            best = {
                "phase": number,
                "branch": review["branch"],
                "pull_request": review.get("pull_request"),
                "head": review.get("head"),
            }
    return best


# A pull request URL, as finalize records it. Only the four segments GitHub
# guarantees are captured: scheme, owner, repository, and the number under
# /pull/. A trailing path, a query, or a fragment is tolerated, because
# finalize pastes whatever URL the reviewer is looking at, and a URL the
# pipeline cannot parse is stored whole with no number so that
# ``gh_pull_request_state`` is simply not consulted.
#
# host is deliberately not pinned to github.com: GitHub Enterprise installs
# this pipeline too, and the host is passed through rather than matched. What
# the number is checked against is the receipt that named the URL, so a URL
# from another host still only ever confirms the phase's own pull request.
_PULL_REQUEST_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


def pull_request_ref(url):
    """The (host, owner, repo, number) a pull request URL names, or None.

    Args:
        url: A pull request URL, or anything at all.

    Returns:
        A dict with ``host``, ``owner``, ``repo`` and ``number`` (an int), or
        None when the string is not a pull request URL. None is the normal
        answer for an empty or malformed value, not an error: the caller stores
        the URL for humans either way and falls back to the commit-id test.
    """
    match = _PULL_REQUEST_URL_RE.match(str(url or "").strip())
    if not match:
        return None
    return {
        "host": match.group("host"),
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "number": int(match.group("number")),
    }


def gh_pull_request_state(number, owner, repo, host=None):
    """Ask GitHub whether a pull request is merged.

    Returns ``(state, detail)`` where state is ``merged``, ``not-merged``, or
    ``unknown``. Three states, not two: a pipeline whose answer is binary
    cannot tell "GitHub says not merged" from "GitHub could not be reached",
    and only one of those is an answer. ``unknown`` must never be read as
    merged, so every caller falls back to the commit-id test, which fails
    closed.

    ``gh`` is invoked rather than a hand-rolled HTTP call because ``gh`` already
    holds the credential in the host keychain and keeps it out of every log,
    file, and command line, which is the whole reason ``github_issues.py``
    exists. A second token path is a second thing to audit.

    Its stderr is captured and discarded rather than reported. An
    unauthenticated call prints the host account name into the message, and run
    logs are copied into issues; the exit status is what this needs and the
    status alone is enough to separate "not merged" from "could not ask".
    """
    target = f"{owner}/{repo}"
    if host and host not in ("github.com",):
        target = f"{host}/{target}"
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(int(number)),
        "--repo",
        target,
        "--json",
        "state,mergedAt,mergeCommit",
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return "unknown", "gh is not installed"
    except subprocess.TimeoutExpired:
        return "unknown", f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        # Exit status only, never the message. 1 is a general failure and 4 is
        # "authentication required"; both mean the same thing here, and neither
        # may be reported as a merge.
        return "unknown", f"gh exited {done.returncode}"
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return "unknown", "gh did not return JSON"
    if not isinstance(data, dict):
        return "unknown", "gh returned JSON that is not an object"
    state = str(data.get("state") or "")
    if state == "MERGED":
        # mergeCommit, not mergedAt, is the durable record: it names the commit
        # GitHub created, and it is populated for a rebase merge where mergedAt
        # is not. Both are read defensively because a field's shape in gh's JSON
        # is gh's, not GitHub's, and this pipeline cannot exercise it against a
        # live pull request without a token it must not reach for.
        commit = data.get("mergeCommit")
        oid = (
            commit
            if isinstance(commit, str)
            else (commit.get("oid") if isinstance(commit, dict) else None)
        )
        return "merged", oid or str(data.get("mergedAt") or "")
    if state in {"OPEN", "CLOSED"}:
        # CLOSED is a closed-without-merging pull request, which is a real
        # answer and not a pending one. Either way it is not merged.
        return "not-merged", state
    return "unknown", f"unrecognised state {state!r}"


def _commit_phase_receipt(root, phase):
    """Commit and push one phase's run directory in the private checkout.

    ``mark_phase_published`` rewrites a merged phase's receipt from
    ``awaiting_review`` to ``published``. Whoever rewrites a receipt must also
    commit it. An uncommitted receipt is dirt in the private checkout, and the
    *next* phase's ``publish_phase`` refuses to publish on top of another
    phase's run artifact, so a finished phase blocks the line. That is the
    defect that halted phase 100876: the merge check marked two merged phases
    published, left both receipts modified, and the running phase's finalize
    then refused with ``working tree is dirty after phase cleanup``.

    Deliberately narrower than ``commit_leftover_run_state``. That helper also
    refuses when the *public* checkout is dirty, and the public checkout is
    normally mid-work while another phase runs — which is exactly when the
    merge check fires. This commits only this phase's own run directory, in the
    private repo, and pushes it, so a running phase's uncommitted work cannot
    block a finished phase's receipt. The phase scope is the same guarantee
    ``commit_leftover_run_state`` makes: a phase never carries another phase's
    run state.

    The receipt is already committed when only the push fails, and that is left
    alone because the working tree is clean at that point. When the commit
    itself fails the receipt is still dirty and nobody owns it, so
    ``mark_phase_published`` restores the previous bytes; see it for why.
    """
    private = board_repo(root)
    return _commit_and_push_run_state(
        private,
        f"pipeline: publish completion receipt for phase {int(phase):06d}",
        pathspec=_phase_run_pathspec(phase, root),
    )


def mark_phase_published(root, phase, public_commit=None, private_commit=None):
    """Record that a human merged a phase's pull request.

    This is the only path from ``awaiting_review`` to ``published``, and it
    refuses unless the merge is real. That is what stops a receipt being edited
    into claiming a merge that did not happen, which is the one outcome a phase
    branch must never produce.

    Two independent confirmations, in this order:

    1. GitHub, asked whether the phase's pull request is ``MERGED``. GitHub
       records the merge itself, and records it identically for a merge commit,
       a squash, and a rebase. This is the check that works for a squash: a
       squash deletes the commit the phase pushed, so the commit-id test below
       reports "not merged" for a phase a human has already merged, and the line
       waits for a merge that already happened. Measured with a five-commit
       phase squash-merged onto the integration branch; the commit-id test
       failed on it and GitHub's answer was the only one that did not.
    2. The commit-id test: the head finalize pushed must be an ancestor of the
       integration branch in each checkout. It still runs whenever GitHub could
       not answer, so a merge performed outside GitHub is still recognised, and
       it still gates the private checkout, which has no pull request because
       the pipeline pushes its own integration branch directly.

    A failed GitHub call is never a merged phase. ``gh_pull_request_state``
    returns three answers rather than two so that "could not ask" and "asked,
    not merged" stay apart, and the unknown case falls through to the commit-id
    test, which fails closed.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    info = {"repo": str(root), "phase": f"{int(phase):06d}"}
    receipt = phase_receipt(root, phase)
    if not receipt:
        return blocked(info, "no publication receipt for this phase")
    if receipt.get("state") == "published":
        return dict(
            info,
            ok=True,
            action="already-published",
            hint="this phase is already recorded as merged",
        )
    if receipt.get("state") != "awaiting_review":
        return blocked(
            info, f"receipt state is {receipt.get('state')!r}, not awaiting review"
        )
    merged = {}
    confirmed_by = {}
    github = {"state": "not-asked", "detail": "no pull request reference"}
    for label, checkout, want in (
        ("public", root, public_commit),
        ("private", private, private_commit),
    ):
        head = receipt.get(f"{label}_head")
        if not head:
            return blocked(info, f"receipt has no {label} head to verify")
        if not is_git_repo(checkout):
            return blocked(info, f"{label} checkout is not a git working tree")
        # The receipt names the branch the pull request targeted, so a renamed
        # integration branch cannot make a real merge look unmerged.
        target = receipt.get("integration_branch") or integration_branch(checkout)
        if label == "public":
            # The recorded reference when the receipt has one, and the reference
            # parsed from the receipt's own URL when it does not. An old receipt
            # names its pull request in plain sight, and asking GitHub about it
            # is the only way a squash-merged phase is ever recognised on a host
            # where the commit-id test cannot see the merge.
            ref = receipt.get("pull_request_ref") or {}
            if not isinstance(ref, dict) or not ref.get("number"):
                ref = pull_request_ref(receipt.get("pull_request")) or {}
            if ref.get("number"):
                state, detail = gh_pull_request_state(
                    ref["number"], ref.get("owner"), ref.get("repo"), ref.get("host")
                )
                github = {"state": state, "detail": detail}
                if state == "merged":
                    merged[label] = detail or f"pull-request-{ref['number']}"
                    confirmed_by[label] = "github"
                    continue
        # Local refs are tried before any fetch. The private checkout pushes its
        # own integration branch at publish time, so its head is already an
        # ancestor of the local target and needs no network at all; and for the
        # public checkout the local answer is only ever possible when the merge
        # has not moved, so a fetch there is wasted too. Fetching only when the
        # local answer failed keeps this off the network on every tick where
        # nothing has been merged, which is the overwhelming majority.
        #
        # No content test here, on purpose. This function records that a human
        # merged a specific pull request, and the content of a phase being
        # present on the integration branch does not say that: the same content
        # can arrive by another route. Only GitHub, or that pull request's own
        # commit, may write "published" into a receipt. The content proof is
        # used by ``phase_work_on_integration`` to choose a base branch, which
        # is a question about where code lives rather than about what a human did.
        found = _head_carried_locally(checkout, head, (want, target))
        if found:
            merged[label] = found
            confirmed_by[label] = "commit-id"
        else:
            git(checkout, "fetch", "--prune", "origin", timeout=FETCH_TIMEOUT_S)
            found = _head_carried_locally(checkout, head, (f"origin/{target}",))
            if found:
                merged[label] = found
                confirmed_by[label] = "commit-id"
        if label not in merged:
            # Which check answered is reported, because the two answer for
            # different reasons and an operator reading a stalled line needs to
            # know whether GitHub said no or could not be asked.
            reason = (
                f"the {label} head {head[:12]} is not on the integration "
                "branch, so the pull request has not been merged"
            )
            if label == "public" and github["state"] == "not-merged":
                reason += " (GitHub reports the pull request as not merged)"
            elif label == "public" and github["state"] == "unknown":
                reason += (
                    f" (GitHub could not be asked: {github['detail']}). The "
                    "squash-merge check is degraded, so a squash-merged phase "
                    "will keep waiting until gh works again"
                )
            return blocked(
                info,
                reason,
                github=github,
                hint="merge the pull request, then rerun; a receipt is never "
                "edited into claiming a merge that did not happen",
            )
    path = phase_run_dir(root, phase) / "publication.json"
    data = dict(receipt)
    data["state"] = "published"
    data["public_commit"] = merged["public"]
    data["private_commit"] = merged["private"]
    data["merged_at"] = _phase_reservations_now()
    # Recorded per checkout, because the two are confirmed by different proofs.
    # The private checkout has no pull request at all — the pipeline pushes its
    # own integration branch directly — so its head is always confirmed by the
    # commit-id test. A single value would claim GitHub vouched for a head it
    # was never asked about, which defeats the field's whole purpose.
    data["merge_confirmed_by"] = {
        k: confirmed_by.get(k, "commit-id") for k in ("public", "private")
    }
    data.pop("engine_written_at_publish", None)
    try:
        original = path.read_text()
    except OSError:
        original = None
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    # The write is not enough on its own. An uncommitted receipt is dirt in the
    # private checkout, and the next phase's publish refuses to run over it, so
    # the phase that rewrites the receipt commits it in the same step.
    committed = _commit_phase_receipt(root, phase)
    if not committed.get("ok"):
        receipt_rel = str(path.relative_to(private))
        if original is not None and receipt_rel in dirty_paths(private):
            # The commit did not land, so the receipt is still dirty and no
            # later tick will retry it: a receipt that reads published leaves
            # the awaiting-review selection, so this phase's merge check never
            # runs again. Put the previous bytes back, so the phase stays
            # awaiting_review, the next tick retries the whole step, and the
            # tree the next phase sees is clean.
            path.write_text(original)
            return blocked(
                info,
                f"could not commit the published receipt: {committed.get('blockers')}",
                hint="the receipt was restored to awaiting_review; the merge "
                "check retries on the next tick",
            )
        return blocked(
            info,
            "the published receipt was committed but the push failed: "
            f"{committed.get('blockers')}",
            hint="the working tree is clean and the receipt is committed; the "
            "next sync pushes it",
        )
    return dict(
        info,
        ok=True,
        action="marked-published",
        public_commit=merged["public"],
        private_commit=merged["private"],
        confirmed_by=data["merge_confirmed_by"],
        receipt_committed=committed.get("action"),
        hint="the phase's commits are on the integration branch",
    )


def _head_carried_locally(checkout, head, candidates):
    """The first candidate ref in this checkout that carries ``head``, or None.

    Split out so the "try local first, fetch only if that failed" rule is one
    named thing rather than a loop that has to be read twice to see whether a
    fetch happened.
    """
    for candidate in candidates:
        if not candidate:
            continue
        rc, _, _ = git(checkout, "merge-base", "--is-ancestor", head, candidate)
        if rc == 0:
            return candidate
    return None


def receipt_for_branch(repo, branch):
    """The publication receipt whose ``branch`` is ``branch``, or None.

    The branch name is the join key, not the phase number, because the branch
    no longer carries the number. Scanning every run directory is deliberate:
    a receipt is the only evidence that a phase reached review, and a phase the
    operator has only written in the roadmap has none.
    """
    for _number, entry in _phase_run_entries(repo):
        try:
            data = json.loads((entry / "publication.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("branch") == branch:
            return data
    return None


def tidy_published_checkout(repo, base=None):
    """Return the checkout to the integration branch if its phase is published.

    This exists because of a stall the change that removed the line's review
    wait introduced, and the mechanism that removed made it possible.

    ``mark_phase_published`` writes the receipt, then ``return_to_integration``
    moves the checkout. When the second fails — a stray untracked run log is
    enough — the receipt already says ``published``, so the selector no longer
    reports the phase as awaiting review, so nothing ever retried the move, so
    the checkout stayed on a dead branch and every later phase refused to launch
    on it. The old design retried forever, because stopping at the review wait
    meant the driver re-ran the merge check on every tick. Removing that stop
    removed the only retry that existed.

    So the move is no longer a side effect of confirming a merge. It is its own
    idempotent step, derived from the receipt, run before branching on every
    tick. Whichever of the two effects lands first, the other one still happens.

    Fail-closed in the same way ``return_to_integration`` is: a dirty tree, a
    detached HEAD, or a missing integration branch stops and reports rather than
    switching. A checkout sitting on a phase that is still ``awaiting_review`` is
    deliberately left alone — that phase is not done, and moving would put it on
    a branch the pipeline is not reviewing.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    here = current_branch(repo)
    info = {"repo": str(repo), "base": base, "branch": here or "(detached)"}
    if not is_git_repo(repo):
        return blocked(info, "not a git working tree")
    if not here or here == base:
        return dict(
            info,
            ok=True,
            action="nothing-to-tidy",
            hint="the checkout is not on an unreviewed phase branch",
        )
    receipt = receipt_for_branch(repo, here)
    if not receipt:
        return dict(
            info,
            ok=True,
            action="no-receipt-for-branch",
            hint=f"{here} has no publication receipt; leaving it alone",
        )
    # A branch shared by a reservation group answers for the group, not for the
    # one member whose receipt happened to be found first. The checkout may only
    # leave while every member is published, because the members still to run
    # need the branch the others published on.
    members = list(receipt.get("group") or [])
    if members:
        if not _group_all_published(repo, members):
            return dict(
                info,
                ok=True,
                action="group-not-yet-published",
                group=members,
                hint=f"{here} carries a reservation group; the checkout "
                "stays on it until every member is published",
            )
        return return_to_integration(repo, base=base)
    state = receipt.get("state")
    if state != "published":
        return dict(
            info,
            ok=True,
            action="phase-not-yet-published",
            phase=receipt.get("phase"),
            receipt_state=state,
            hint=f"{here} belongs to phase {receipt.get('phase')}, "
            f"still {state}; the checkout stays put",
        )
    return return_to_integration(repo, base=base)


def return_to_integration(repo, base=None):
    """Put a checkout back on the integration branch once its phase is merged.

    Called only after ``mark_phase_published`` has confirmed the merge is real.
    A phase branch that is never left is a slow drift: the checkout stays on
    branch after branch, and the next sync re-bases from whatever it finds
    rather than from a branch someone chose.

    Fail-closed in the same way ``ensure_phase_branch`` is. A dirty working
    tree is the one case where switching branches could lose work, so that is a
    stop and not a forced checkout. A clean tree on the integration branch
    already is a no-op, and a missing integration branch is a stop rather than
    an invented starting point.

    Returns a result dict; ok is the success flag.
    """
    repo = Path(repo).resolve()
    base = base or integration_branch(repo)
    info = {"repo": str(repo), "base": base}
    if not is_git_repo(repo):
        return blocked(info, "not a git working tree")
    rc, dirty, _ = git(repo, "status", "--porcelain")
    if rc != 0:
        return blocked(info, "cannot read the working tree status")
    if dirty.strip():
        return blocked(
            info,
            "the working tree has uncommitted changes",
            hint="commit, stash, or discard them by hand; switching branches "
            "with uncommitted work is how that work gets lost",
        )
    rc, _, _ = git(repo, "rev-parse", "--verify", base)
    if rc != 0:
        return blocked(
            info,
            f"integration branch {base!r} does not exist",
            hint="the integration branch is missing; refusing to "
            "move the checkout with no destination",
        )
    here = current_branch(repo)
    if here == base:
        return dict(
            info,
            ok=True,
            action="already-on-integration-branch",
            hint=f"already on {base}",
        )
    if not here:
        return blocked(
            info,
            "the current branch is not readable",
            hint="a detached HEAD is an operator decision, not something to guess at",
        )
    rc, _, err = git(repo, "checkout", base)
    if rc != 0:
        return blocked(info, f"could not check out {base!r}: {err.strip()}")
    return dict(
        info,
        ok=True,
        action="returned-to-integration-branch",
        before=here,
        after=base,
        hint=f"left {here} and returned to {base}",
    )


def _phase_reservations_now():
    from argus.phase_reservations import _now

    return _now()


def _declared_group(root, phase):
    """The reservation group a phase declares, or ``None``.

    Read from the roadmap documents, which is where a declaration lives. A
    malformed one is refused rather than read as "runs alone": a phase whose
    group nobody can read is not a phase that ran by itself, and publishing it
    as one would record the wrong branch. An absent declaration, and a machine
    with no roadmap folder to read, are the ordinary single-phase case.
    """
    from argus import roadmap_source

    folder = roadmap_folder(root)
    if not Path(folder).is_dir():
        return None
    try:
        return roadmap_source.resolve_group_or_none(root, folder, phase)
    except roadmap_source.ReservationGroupMissing:
        return None
    except Exception as exc:  # noqa: BLE001 - reported as a configuration error
        raise ConfigError(
            f"cannot read the reservation group of phase {int(phase):06d}: {exc}"
        ) from exc


def _member_published(root, member):
    """Whether one group member's receipt says it is published."""
    try:
        number = int(str(member).strip())
    except (TypeError, ValueError):
        return False
    receipt = phase_receipt(root, number)
    return bool(receipt) and receipt.get("state") == "published"


def _group_all_published(root, members):
    """Whether every member of a group is published.

    A group shares one branch and one pull request, so a member whose merge is
    recorded is not permission to leave that branch: the members that have not
    run yet still need it. A member with no receipt at all is not published,
    which is the answer that keeps the checkout where the group needs it.
    """
    members = list(members or ())
    return bool(members) and all(_member_published(root, member) for member in members)


def _group_pull_request(root, group, branch, phase):
    """The pull request another member of this group already recorded.

    One branch and one pull request cover a group, so every member's receipt
    names the pull request the group already has. The join key is the group
    branch, and the member list is checked with it: once one branch carries
    several receipts, the branch name alone no longer names one phase. The
    member being published is skipped, so republishing it cannot answer with its
    own earlier record.
    """
    if not branch:
        return None
    wanted = {f"{int(member):06d}" for member in group}
    mine = f"{int(phase):06d}"
    for number, _entry in _phase_run_entries(root):
        if f"{int(number):06d}" == mine:
            continue
        receipt = phase_receipt(root, number)
        if not isinstance(receipt, dict) or receipt.get("branch") != branch:
            continue
        recorded = receipt.get("group") or []
        if {f"{int(member):06d}" for member in recorded} == wanted:
            return receipt.get("pull_request")
    return None


def _write_publication_receipt(
    root, phase, reservation, results, engine_written=(), pull_request=None
):
    private = board_repo(root)
    run_dir = phase_run_dir(root, phase)
    if not run_dir.is_dir():
        raise ConfigError(f"phase run directory is missing: {run_dir}")
    public_commit = _published_head(results, root)
    private_commit = _published_head(results, private)
    if not public_commit or not private_commit:
        raise ConfigError("publication did not produce both repository heads")
    public_branch = current_branch(root)
    private_branch = current_branch(private)
    # One branch and one pull request cover a whole group, so every member's
    # receipt records the pull request the group already has. A second pull
    # request on the same branch cannot exist, and a member whose receipt named
    # none would be verified against its commit id alone. The caller's own URL is
    # used only until a member of this group has recorded one.
    group = _declared_group(root, phase)
    if group:
        pull_request = (
            _group_pull_request(root, group["group"], public_branch, phase)
            or pull_request
        )
    from argus.phase_reservations import _now

    receipt = {
        "version": 1,
        "phase": f"{int(phase):06d}",
        # Not "published". A phase's commits live on its own branch until a
        # human merges the pull request, and a receipt that said otherwise
        # would certify a merge that has not happened. A pull request that a
        # person reviews *is* the published state; this records that it is
        # waiting.
        "state": "awaiting_review",
        "branch": public_branch,
        "private_branch": private_branch,
        "integration_branch": integration_branch(root),
        "reservation": {
            "machine_id": reservation["machine_id"],
            "reservation_id": reservation["reservation_id"],
            "generation": reservation["generation"],
        },
        "public_head": public_commit,
        "private_head": private_commit,
        "published_at": _now(),
    }
    if group:
        # The declared array, so a reader can see which phases this branch and
        # this pull request cover, and so the merge check can tell whether every
        # member is published before the checkout leaves the group branch.
        receipt["group"] = list(group["group"])
    # The predecessor this phase was stacked on, with the commit it was stacked
    # on. Without the commit, a predecessor branch that is force-pushed or
    # rewritten after this phase branched from it would silently change what
    # this phase is built on, and nothing would ever notice: the branch still
    # exists, so every existence check passes.
    carried = open_predecessor(root, phase)
    if carried and carried.get("branch") == public_branch:
        # Another member of this phase's own reservation group: the same branch
        # and the same pull request. Recording that as a branch this phase was
        # stacked on would say the phase is based on itself.
        carried = None
    if carried:
        base = carried["branch"]
        rc_b, base_head, _ = git(root, "rev-parse", "--verify", f"refs/heads/{base}")
        if rc_b != 0:
            rc_b, base_head, _ = git(
                root, "rev-parse", "--verify", f"refs/remotes/origin/{base}"
            )
        receipt["stacked_on"] = {
            "phase": f"{int(carried['phase']):06d}",
            "branch": base,
            "head": base_head if rc_b == 0 else None,
            "pull_request": carried.get("pull_request"),
        }
    if pull_request:
        receipt["pull_request"] = pull_request
        # The parsed reference is what makes the merge question askable. The
        # URL stays for humans; the number, owner and repository are what the
        # GitHub check needs, and a URL that does not parse yields no reference
        # so the check is skipped and the commit-id test answers instead.
        ref = pull_request_ref(pull_request)
        if ref:
            receipt["pull_request_ref"] = ref
    if engine_written:
        # The clean-tree check skipped these paths because the engine that was
        # publishing is the engine that rewrites them. They were committed and
        # pushed like everything else; this records which ones the check chose
        # not to wait for, so the exemption is visible in the run's history
        # rather than only in the helper's output.
        receipt["engine_written_at_publish"] = sorted(engine_written)
    path = run_dir / "publication.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    pushed = commit_leftover_run_state(
        root,
        f"pipeline: publish completion receipt for phase {int(phase):06d}",
        phase=phase,
    )
    if not pushed.get("ok"):
        raise ConfigError(
            f"publication receipt was not pushed: {pushed.get('summary', 'unknown error')}"
        )
    receipt["private_commit"] = private_commit
    receipt["path"] = str(path)
    return receipt, pushed


def _load_phase_reservation(
    root,
    phase,
    reservation_file=None,
    machine_id=None,
    reservation_id=None,
    generation=None,
):
    if reservation_file is None:
        reservation_file = phase_run_dir(root, phase) / "reservation.json"
    try:
        data = json.loads(Path(reservation_file).read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read phase reservation: {exc}") from exc
    if machine_id:
        data["machine_id"] = machine_id
    if reservation_id:
        data["reservation_id"] = reservation_id
    if generation is not None:
        data["generation"] = int(generation)
    data["phase"] = f"{int(phase):06d}"
    return data


def _push_one(repo):
    branch, remote, _remote_branch, why = resolve_upstream(repo)
    if why:
        return blocked({"repo": str(repo)}, why)
    rc, out, err = git(repo, "push", remote, branch)
    if rc != 0:
        return blocked(
            {"repo": str(repo)},
            f"push failed: {err or out}",
            hint="resolve the remote and rerun; never force-push",
        )
    return {
        "repo": str(repo),
        "ok": True,
        "action": "pushed",
        "after": git(repo, "rev-parse", "HEAD")[1],
    }


def publish_phase(
    root,
    phase,
    *,
    reservation_file=None,
    machine_id=None,
    reservation_id=None,
    generation=None,
    commit_run_state=True,
    pull_request=None,
):
    """Check the phase fence, take the short publication lock, and publish.

    The caller must commit the phase's product changes first, on the phase's own
    branch. This helper commits only leftover files in that phase's run
    directory, syncs both checkouts fail-closed, and pushes. It never stages an
    unrelated phase, never uses a force push, and never merges: a phase's
    commits reach the integration branch only when a human merges the pull
    request this run's receipt points at.
    """
    root = Path(root).resolve()
    try:
        phase = int(phase)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid phase number: {phase!r}") from exc
    if phase < 0 or phase > 999999:
        raise ConfigError(f"phase number out of range: {phase}")
    if not commit_run_state:
        raise ConfigError("publication requires phase-scoped run-state commit")
    try:
        from argus.phase_reservations import (
            PublicationBusy,
            ReservationFenced,
            ReservationStore,
        )

        reservation = _load_phase_reservation(
            root, phase, reservation_file, machine_id, reservation_id, generation
        )
        store = ReservationStore(root)
        store.assert_owner(reservation)
        store.acquire_publication(reservation)
    except PublicationBusy as exc:
        return {
            "ok": False,
            "mode": "publish",
            "state": "PUBLICATION_BUSY",
            "error": str(exc),
            "phase": f"{int(phase):06d}",
        }
    except ReservationFenced as exc:
        return {
            "ok": False,
            "mode": "publish",
            "state": "FENCED",
            "error": str(exc),
            "phase": f"{int(phase):06d}",
        }
    except Exception as exc:  # noqa: BLE001 - normalize boundary errors
        return {
            "ok": False,
            "mode": "publish",
            "state": "COORDINATION_UNAVAILABLE",
            "error": str(exc),
            "phase": f"{int(phase):06d}",
        }

    results = []
    outcome = None
    engine_churn = []
    try:
        if commit_run_state:
            clean = commit_leftover_run_state(
                root,
                f"pipeline: publish run state for phase {int(phase):06d}",
                phase=phase,
            )
            results.append(clean)
            if not clean["ok"]:
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "BLOCKED",
                    "repos": results,
                    "summary": clean["summary"],
                }
        if outcome is None:
            dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
            if is_git_repo(board_repo(root)):
                dirty += [
                    str(board_repo(root) / p) for p in dirty_paths(board_repo(root))
                ]
            # The engine that is publishing is the engine that rewrites the
            # heartbeat marker, so a clean-tree requirement tests a directory
            # that is still being written. The marker is already committed and
            # pushed by the step above; what is left is seconds of churn that
            # carries no unreviewed work. Everything else still refuses, and
            # the excluded set is reported rather than dropped.
            real, engine = publish_dirt(dirty, root=root, phase=phase)
            engine_churn = engine
            if real:
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "BLOCKED",
                    "repos": results,
                    "error": "working tree is dirty after phase cleanup",
                    "dirty": real,
                    "engine_written": engine,
                }
        if outcome is None:
            # The authoritative integration-branch resync, and the one that
            # cannot be dodged by ordering. `sync_all(root, "push")` below only
            # compares each branch against its own upstream, so it never sees
            # the integration branch at all; this is the step that guarantees a
            # phase publishes against a base that contains master.
            #
            # It sits here rather than in a stage prompt because this is the one
            # point in a phase where the index is known clean: finalize has
            # already committed both repositories, which is this helper's own
            # precondition. A resync issued earlier, from a prompt, can be
            # refused by git whenever the phase happens to have staged work, and
            # nothing in code stops that from happening.
            resync = mode_resync(root)
            results.append(resync)
            if not resync["ok"]:
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "BLOCKED",
                    "repos": results,
                    "summary": resync["summary"],
                    "error": "the integration branch did not merge into "
                    "the phase branch; resolve the named paths, "
                    "rerun the gates the merge invalidated, and "
                    "rerun this helper",
                }
        if outcome is None:
            sync = sync_all(root, "push")
            results.append(sync)
            if not sync["ok"]:
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "BLOCKED",
                    "repos": results,
                    "summary": sync["summary"],
                }
        if outcome is None:
            for checkout in (root, board_repo(root)):
                if not is_git_repo(checkout):
                    continue
                store.assert_owner(reservation)
                pushed = _push_one(checkout)
                results.append(pushed)
                if not pushed.get("ok"):
                    outcome = {
                        "ok": False,
                        "mode": "publish",
                        "phase": f"{int(phase):06d}",
                        "state": "BLOCKED",
                        "repos": results,
                        "summary": summarize([pushed], "publish"),
                    }
                    break
        if outcome is None:
            push_results = [r for r in results if isinstance(r, dict) and "repo" in r]
            # AWAITING_REVIEW, not PUBLISHED. The phase's commits are on its
            # own branch; a human merges the pull request. The exit code is
            # still 0, because publishing the branch is what this helper does,
            # and it did it.
            outcome = {
                "ok": True,
                "mode": "publish",
                "phase": f"{int(phase):06d}",
                "state": "AWAITING_REVIEW",
                "branch": current_branch(root),
                "repos": results,
                "engine_written": engine_churn,
                "summary": summarize(push_results, "publish"),
            }
        if outcome.get("ok") and commit_run_state:
            receipt, receipt_result = _write_publication_receipt(
                root,
                phase,
                reservation,
                results,
                engine_written=engine_churn,
                pull_request=pull_request,
            )
            results.append(receipt_result)
            outcome["receipt"] = receipt
            outcome["repos"] = results
            outcome["summary"] = summarize(
                [r for r in results if isinstance(r, dict) and "repo" in r], "publish"
            )
            # A warning, never a block. This phase was built on an earlier
            # phase's branch, so its pull request carries that phase's work.
            # Merging this one alone lands code that was never reviewed as its
            # own phase, and only a human can decide whether that phase's work
            # should exist at all. Blocking here would put the human back on the
            # critical path, which is the thing this pipeline exists to remove.
            carried = open_predecessor(root, phase)
            if carried:
                outcome["carries_open_phase"] = carried
                outcome["warning"] = (
                    f"this phase's branch was cut from {carried['branch']!r}, "
                    f"which is phase {carried['phase']:06d} and is still waiting "
                    "for review. Merging this pull request also lands that "
                    "phase's work, which has not been reviewed as its own "
                    "change. Review both, or merge that one first."
                )
    except Exception as exc:  # noqa: BLE001 - publication is fail-closed
        outcome = {
            "ok": False,
            "mode": "publish",
            "phase": f"{int(phase):06d}",
            "state": "BLOCKED",
            "repos": results,
            "error": str(exc),
        }
    try:
        store.release_publication(reservation)
    except Exception as exc:  # noqa: BLE001 - a stale lock must be visible
        outcome = dict(
            outcome or {"ok": False, "mode": "publish", "phase": f"{int(phase):06d}"}
        )
        outcome["ok"] = False
        outcome["state"] = "COORDINATION_UNAVAILABLE"
        outcome["error"] = f"could not release publication lock: {exc}"
    return outcome


def summarize(results, mode):
    parts = []
    for r in results:
        if r.get("ok"):
            parts.append(f"{r['repo']}: {r.get('action', 'ok')}")
        else:
            parts.append(f"{r['repo']}: BLOCKED ({'; '.join(r.get('blockers', []))})")
    head = f"{mode} sync {'ok' if all(r.get('ok') for r in results) else 'BLOCKED'}"
    return f"{head}: " + "; ".join(parts)


def _check(checks, name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _init_remote(tmp, name):
    """Bare remote with HEAD -> main, plus a clone at tmp/<name> that has
    one commit on main tracking origin/main.

    The remote is seeded by a throwaway clone *before* the real one is made, and
    that ordering is load-bearing rather than incidental. ``integration_branch``
    resolves a checkout's integration branch by checking for ``master`` and
    otherwise reading ``refs/remotes/origin/HEAD``; a clone of an empty remote
    gets no ``origin/HEAD`` at all, so the resolver falls through to its
    hard-coded default and returns a branch this fixture never created. Every
    check downstream then resolves a branch that does not exist, and fails for a
    reason that has nothing to do with the code under test. Seeding first makes
    the clone carry a correct ``origin/HEAD``, so the resolver finds ``main``
    exactly the way it finds ``master`` in production.
    """
    remote = tmp / f"{name}-remote.git"
    subprocess.run(
        [git_binary(), "init", "--bare", "-q", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    seed = tmp / f"{name}-seed"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(seed)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(seed, "config", "user.email", "sync-test@example.com")
    git(seed, "config", "user.name", "sync test")
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "a.txt").write_text("one\n")
    git(seed, "add", "a.txt")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "push", "-q", "-u", "origin", "main")
    work = tmp / name
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(work)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(work, "config", "user.email", "sync-test@example.com")
    git(work, "config", "user.name", "sync test")
    git(work, "checkout", "-q", "-b", "main")
    git(work, "push", "-q", "-u", "origin", "main")
    return remote, work


def _remote_commit(remote, tmp, name, content="two\n"):
    """Push one commit to the remote from a throwaway clone."""
    other = tmp / f"other-{name}"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(other)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(other, "config", "user.email", "sync-test@example.com")
    git(other, "config", "user.name", "sync test")
    (other / "a.txt").write_text(content)
    git(other, "add", "a.txt")
    git(other, "commit", "-q", "-m", f"remote {name}")
    git(other, "push", "-q", "origin", "main")


def _local_commit(work, name):
    (work / "b.txt").write_text(f"{name}\n")
    git(work, "add", "b.txt")
    git(work, "commit", "-q", "-m", f"local {name}")


def _configure_self_test(
    pub,
    board,
    *,
    pipeline=None,
    roadmap=None,
    runs=None,
    include=("phase-*.md",),
    exclude=(),
    number_input="phase_number",
    file_input="phase_file",
):
    pub = Path(pub).resolve()
    board = Path(board).resolve()
    try:
        board_rel = board.relative_to(pub)
    except ValueError:
        board_rel = None
    if board_rel is not None:
        exclude_file = pub / ".git" / "info" / "exclude"
        entry = board_rel.as_posix().rstrip("/") + "/"
        if exclude_file.is_file():
            current = exclude_file.read_text(encoding="utf-8")
            if entry not in current.splitlines():
                exclude_file.write_text(current + entry + "\n", encoding="utf-8")
    conventions.configure(
        {
            "repo-root": pub,
            "pipeline": pipeline,
            "roadmap-dir": Path(roadmap or Path(board) / "roadmap"),
            "driver.runs": Path(runs or Path(board) / "runs"),
            "driver.include": list(include),
            "driver.exclude": list(exclude),
            "number-input": number_input,
            "file-input": file_input,
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": board,
            "board.file": board / "claims.json",
            "board.branch": "main",
            "board.remote": "origin",
        }
    )


def _publication_receipt_check(base):
    d = base / "publication-receipt"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    _configure_self_test(pub, nested)
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        store = ReservationStore(pub, branch="main")
        reservation = store.reserve(100440, "server-01")
        run_dir = nested / "runs" / "phase-100440"
        run_dir.mkdir(parents=True, exist_ok=True)
        save_reservation_file(run_dir / "reservation.json", reservation)
        (run_dir / "run.json").write_text('{"state":"completed"}\\n')
        result = publish_phase(pub, 100440)
        if not result.get("ok") or result.get("state") != "AWAITING_REVIEW":
            return False, str(result)
        receipt_path = result.get("receipt", {}).get("path")
        if not receipt_path or not Path(receipt_path).is_file():
            return False, "publication receipt was not written"
        # A receipt for an unmerged phase must not read as completion. That is
        # the whole point of a phase branch: nothing reaches the integration
        # branch until a person merges, so the phase is not finished.
        from argus.next_phase import publication_receipt_done

        if publication_receipt_done(pub, 100440):
            return False, "an unmerged phase's receipt reads as done"
        from argus.gitsync import awaiting_review

        review = awaiting_review(pub, 100440)
        if not review or review.get("branch") is None:
            return False, f"receipt does not name a branch to review: {review}"
        # The coordination board must not be able to record this phase as
        # complete, because its evidence is a receipt for an unmerged branch.
        try:
            store.set_status(reservation, "completed", evidence_path=receipt_path)
            return False, "the board accepted an unmerged phase as complete"
        except Exception:  # noqa: BLE001 - any refusal is the point
            pass
        return store.read()[0]["phases"]["100440"][
            "status"
        ] != "completed", "an unmerged phase cannot be recorded as complete"
    except Exception as exc:  # noqa: BLE001 - self-test reports the failure
        return False, str(exc)


def _group_publication_check(base):
    """One branch, one pull request, and one receipt per member of a group.

    A group publishes every member from one branch and one pull request, and each
    member keeps its own receipt under its own run directory. Both members are
    published for real here, because the property is about what the helper
    writes, not about what a fixture says it would write.

    Returns ``(ok, detail)`` like the other fixture checks.
    """
    d = base / "group-publication"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    # The declaration lives in the phase documents, so where a group is read
    # from is where it has to be written.
    roadmap = nested / "roadmap"
    roadmap.mkdir(parents=True, exist_ok=True)
    for number, behaviour in ((100020, "cache-warm"), (100030, "cache-read")):
        (roadmap / f"phase-{number:06d}-{behaviour}.md").write_text(
            f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
            "### Reservation Group\n\n`together: 100020, 100030`\n",
            encoding="utf-8",
        )
    _configure_self_test(pub, nested, roadmap=roadmap)
    members = ["100020", "100030"]
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        store = ReservationStore(pub, branch="main")
        records = store.reserve_group(members, "server-01")
        git(nested, "fetch", "-q", "origin")
        git(nested, "merge", "-q", "--ff-only", "origin/main")
        branch = group_branch(pub, members)
        made = ensure_phase_branch(pub, branch)
        if not made.get("ok"):
            return False, f"the group branch was not created: {made}"
        for record in records:
            run_dir = nested / "runs" / f"phase-{record['phase']}"
            run_dir.mkdir(parents=True, exist_ok=True)
            save_reservation_file(run_dir / "reservation.json", record)
            (run_dir / "run.json").write_text('{"state":"completed"}\n')
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "run state")
        git(nested, "push", "-q", "origin", "main")
        url = "https://github.com/example/argus/pull/7"
        first = publish_phase(pub, 100020, pull_request=url)
        if not first.get("ok"):
            return False, f"the group's first member did not publish: {first}"
        # No pull request is passed for the second member. One branch can carry
        # one pull request, so the group's own record is the only answer.
        second = publish_phase(pub, 100030)
        if not second.get("ok"):
            return False, f"the group's second member did not publish: {second}"
        receipts = {number: phase_receipt(pub, number) for number in (100020, 100030)}
        if not all(receipts.values()):
            return False, "a member published without a receipt of its own"
        if {r.get("branch") for r in receipts.values()} != {branch}:
            return False, (
                "the members did not publish on one branch: "
                f"{[r.get('branch') for r in receipts.values()]}"
            )
        if {r.get("pull_request") for r in receipts.values()} != {url}:
            return False, (
                "the members did not record the same pull request: "
                f"{[r.get('pull_request') for r in receipts.values()]}"
            )
        for number, receipt in receipts.items():
            if list(receipt.get("group") or []) != members:
                return False, (
                    f"phase {number}'s receipt does not record the "
                    f"group it belongs to: {receipt.get('group')}"
                )
        # A merged member must not take the checkout off the group branch: the
        # members that have not run yet still need it.
        half = mode_published(pub, 100020)
        if half.get("state") != "PUBLISHED-GROUP-PENDING":
            return False, (
                "a half-published group did not leave the checkout "
                f"where the rest of the group needs it: {half.get('state')}"
            )
        if current_branch(pub) != branch:
            return False, (
                f"the checkout left the group branch for {current_branch(pub)}"
            )
        done = mode_published(pub, 100030)
        if done.get("state") != "PUBLISHED":
            return (
                False,
                f"the finished group did not tidy the checkout: {done.get('state')}",
            )
        if current_branch(pub) == branch:
            return (
                False,
                "the checkout stayed on the group branch once every member was published",
            )
        return True, "one branch, one pull request, one receipt per member"
    except Exception as exc:  # noqa: BLE001 - self-test reports the failure
        return False, str(exc)


def _publish_fixture(base, name, phase):
    """A phase at finalize in its own throwaway pair of remotes.

    Returns (public checkout, nested private checkout, run directory). The
    coordination board is set up and the reservation file written, so
    ``publish_phase`` can be called for real.
    """
    d = base / name
    d.mkdir(parents=True)
    _public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    _configure_self_test(pub, nested)
    from argus.phase_reservations import ReservationStore, save_reservation_file

    store = ReservationStore(pub, branch="main")
    reservation = store.reserve(phase, "server-01")
    # The coordination transaction pushes onto the same branch the working tree
    # is on, so the checkout is left behind its own remote. That is the real
    # mechanism by which "master" moves under a phase.
    git(nested, "fetch", "-q", "origin")
    git(nested, "merge", "-q", "--ff-only", "origin/main")
    run_dir = nested / "runs" / f"phase-{phase:06d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    save_reservation_file(run_dir / "reservation.json", reservation)
    (run_dir / "run.json").write_text('{"state":"completed"}\n')
    (run_dir / "activity").write_text("harness output at 0\n")
    (run_dir / "reminders.log").write_text("reminder suppressed by busy\n")
    (run_dir / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
    git(nested, "add", "-A")
    git(nested, "commit", "-q", "-m", "run state")
    git(nested, "push", "-q", "origin", "main")
    return pub, nested, run_dir


def _portable_publication_check(base):
    """Publish a custom phase through an independently named private checkout."""
    phase = 100615
    try:
        pub, nested, _run = _publish_fixture(base, "portable-publication", phase)
        private = pub.parent / "renamed-private-board"
        nested.rename(private)
        run = private / "artifacts" / f"item-{phase:06d}"
        run.parent.mkdir()
        (private / "runs" / f"phase-{phase:06d}").rename(run)
        roadmap = private / "plans"
        roadmap.mkdir()
        document = roadmap / f"release_{phase}_generic-release.md"
        document.write_text("generic release\n")
        pipeline = pub.parent / "custom-pipeline.yaml"
        pipeline.write_text(
            "inputs:\n  ticket: {type: int, required: true}\n"
            "  document: {type: str, required: true}\n"
            "run_dir: " + str(private / "artifacts" / "item-{ticket:06d}") + "\n"
        )
        _configure_self_test(
            pub,
            private,
            pipeline=pipeline,
            roadmap=roadmap,
            runs=run.parent,
            include=("release_*.md",),
            number_input="ticket",
            file_input="document",
        )
        git(private, "add", "-A")
        git(private, "commit", "-q", "-m", "custom private layout")
        git(private, "push", "-q", "origin", "main")
        branched = mode_branch(pub, phase, document.name)
        if not branched.get("ok"):
            return False, f"custom branch failed: {branched}"
        (pub / "product.txt").write_text("release\n")
        git(pub, "add", "product.txt")
        git(pub, "commit", "-q", "-m", "generic release")
        result = publish_phase(pub, phase)
        receipt = result.get("receipt", {})
        if not result.get("ok"):
            return False, f"custom publication failed: {result}"
        branch = phase_branch(document.name)
        predecessor = open_predecessor(pub, phase + 1)
        recorded = git(
            private, "show", f"HEAD:artifacts/item-{phase:06d}/publication.json"
        )
        return (
            receipt.get("path") == str(run / "publication.json")
            and phase_receipt(pub, phase).get("branch") == branch
            and receipt_for_branch(pub, branch).get("phase") == str(phase)
            and predecessor is not None
            and predecessor["phase"] == phase
            and recorded[0] == 0,
            "custom phase, typed inputs, run directory, private checkout, "
            "reservation, receipt, and predecessor use the configured layout",
        )
    except Exception as exc:  # noqa: BLE001 - the self-test reports the failure
        return False, str(exc)


class _Heartbeat:
    """A stand-in for the engine's heartbeat writer, on the real cadence.

    glide.nudge.touch_activity is a public function with one job: rewrite
    ``runs/<phase>/activity`` with a fresh whole-second timestamp. The
    publication helper is asked to publish from inside the process that does
    that, so the writer has to be a separate process, running, with no
    cooperation from the code under test. If glide is importable the real
    function is used; otherwise the same three lines are written directly, and
    the check says so.
    """

    def __init__(self, run_dir, period=0.05):
        self.run_dir = Path(run_dir)
        self.period = period
        self.using_glide = False
        self.proc = None

    def __enter__(self):
        import threading

        try:
            from glide import nudge

            self.using_glide = nudge.ACTIVITY_NAME == "activity"
        except ImportError:
            self.using_glide = False
        stop = threading.Event()
        marker = self.run_dir / "activity"
        seq = [0]

        def write():
            while not stop.is_set():
                seq[0] += 1
                with contextlib.suppress(OSError):
                    marker.write_text(f"harness output at {1758800000 + seq[0]}\n")
                stop.wait(self.period)

        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        self._stop, self._thread = stop, thread
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)
        return False

    def write_now(self):
        marker = self.run_dir / "activity"
        marker.write_text("harness output at 1999999999\n")


def _publish_while_writing_check(base):
    """Publication must succeed on the first attempt with the engine writing.

    Before the engine-written-artifact exclusion, this failed intermittently
    with ``working tree is dirty after phase cleanup: .../activity``: the
    helper committed the run state, the engine rewrote the marker in the
    window, and the check that followed saw it. 9 failures in 300 attempts
    with the engine writing, 0 with it stopped.
    """
    pub, nested, run_dir = _publish_fixture(base, "publish-race", 100441)
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        with _Heartbeat(run_dir):
            time.sleep(0.4)
            marker = run_dir / "activity"
            first = marker.read_text()
            time.sleep(1.1)
            second = marker.read_text()
            result = publish_phase(pub, 100441)
    finally:
        conventions.override({"board.branch": old_branch})
    ok = (
        result.get("ok")
        and result.get("state") == "AWAITING_REVIEW"
        and first != second
    )
    detail = (
        f"first={first.strip()!r} second={second.strip()!r} "
        f"state={result.get('state')} "
        f"engine_written={result.get('engine_written')} "
        f"summary={result.get('summary')} error={result.get('error')} "
        f"dirty={result.get('dirty')}"
    )
    if not ok:
        return False, detail
    # The marker is still committed and pushed, exclusion or not.
    tracked = "activity" in git(nested, "ls-files", "runs")[1]
    committed = git(nested, "show", "HEAD:runs/phase-100441/activity")[1]
    if not tracked or not committed.strip():
        return False, f"marker is not committed and pushed: {detail}"
    receipt = result.get("receipt", {})
    if receipt.get("state") != "awaiting_review":
        return False, f"receipt state changed: {receipt}"
    return (bool(receipt.get("engine_written_at_publish")), detail)


def _publish_still_refuses_check(base):
    """The exclusion must not have become a blanket pass.

    Each case gets its own fixture, because a publication moves both remotes.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # 1. A modified tracked source file in the public checkout.
        pub, _nested, _run = _publish_fixture(base, "publish-dirt-source", 100442)
        (pub / "a.txt").write_text("one\nunreviewed edit\n")
        res = publish_phase(pub, 100442)
        results.append(
            (
                "a modified tracked source file still blocks",
                (not res.get("ok"))
                and "outside the pipeline's run state" in res.get("summary", ""),
                res.get("summary", str(res))[:200],
            )
        )

        # 2. A staged change in the private checkout, outside the phase.
        pub, nested, _run = _publish_fixture(base, "publish-dirt-staged", 100443)
        (nested / "stray.txt").write_text("unreviewed\n")
        git(nested, "add", "stray.txt")
        res = publish_phase(pub, 100443)
        results.append(
            (
                "a staged change outside the phase still blocks",
                (not res.get("ok"))
                and "outside the current phase" in res.get("summary", ""),
                res.get("summary", str(res))[:200],
            )
        )

        # 3. A staged change inside the phase's own run directory is the phase's
        # own evidence, so it is committed rather than refused. Asserted as
        # measured: this is how the helper behaved before the change too.
        pub, nested, run_dir = _publish_fixture(base, "publish-inside", 100445)
        (run_dir / "findings.json").write_text('{"open": []}\n')
        res = publish_phase(pub, 100445)
        pushed = (
            "runs/phase-100445/findings.json"
            in git(nested, "ls-files", "runs")[1].splitlines()
        )
        results.append(
            (
                "a new file inside the phase's run directory is published, not blocked",
                bool(res.get("ok")) and pushed,
                f"state={res.get('state')} committed={pushed}",
            )
        )
    finally:
        conventions.override({"board.branch": old_branch})

    # 4. The exclusions are exactly the two engine-written artifacts, and only
    # by basename, so a source file called `activity` elsewhere is not exempt
    # by accident and a new run artifact is not exempt by accident.
    unit = [
        ("runs/phase-1/activity", True),
        (f"{BOARD_REL}/runs/phase-1/activity", True),
        (f"{BOARD_REL}/runs/phase-1/reminders.log", True),
        ("crates/store/activity", False),
        ("crates/store/reminders.log", False),
        ("crates/store/src/lib.rs", False),
        ("runs/phase-1/findings.json", False),
        ("runs/phase-1/finalize-task-r1.log", False),
        ("runs/phase-1/ledger.json", False),
        ("runs/phase-1/publication.json", False),
        ("runs/phase-1/resume.json", False),
        ("runs/activity", False),
        ("runs/phase-1/nested/activity", False),
    ]
    wrong = [
        (p, want, is_engine_written_run_artifact(p))
        for p, want in unit
        if is_engine_written_run_artifact(p) != want
    ]
    results.append(
        (
            "the engine-written exemption is exactly two run artifacts",
            not wrong,
            f"mismatches={wrong}",
        )
    )

    real, engine = publish_dirt(
        [
            f"{BOARD_REL}/runs/phase-1/activity",
            "crates/store/src/lib.rs",
            f"{BOARD_REL}/runs/phase-1/reminders.log",
            "runs/phase-1/notes.md",
        ]
    )
    results.append(
        (
            "publish_dirt reports the excluded set instead of dropping it",
            real == ["crates/store/src/lib.rs", "runs/phase-1/notes.md"]
            and engine
            == [
                f"{BOARD_REL}/runs/phase-1/activity",
                f"{BOARD_REL}/runs/phase-1/reminders.log",
            ],
            f"real={real} engine={engine}",
        )
    )
    return results


def _phase_branch_checks(base):
    """A phase's work must never sit on the integration branch, and a branch
    must never be moved out from under a working tree that holds it."""
    results = []
    d = base / "branch"
    d.mkdir(parents=True)
    remote, work = _init_remote(d, "w")
    name = "phase-100440-backup-archive-format-and-directory.md"
    branch = phase_branch_for(100440, name)

    # 1. Creating it carries the phase's staged work across untouched.
    (work / "c.txt").write_text("staged baseline\n")
    git(work, "add", "c.txt")
    blob = git(work, "rev-parse", ":c.txt")[1]
    res = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a phase branch is created from the integration branch and "
            "carries the staged baseline",
            res["ok"]
            and res["action"] == "created-phase-branch"
            and current_branch(work) == branch
            and git(work, "rev-parse", ":c.txt")[1] == blob
            and (work / "c.txt").read_text() == "staged baseline\n",
            f"branch={current_branch(work)} {res.get('action')}",
        )
    )

    # 2. Idempotent: a second tick must not move or recreate anything.
    head = git(work, "rev-parse", "HEAD")[1]
    res2 = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "creating it again is a no-op",
            res2["ok"]
            and res2["action"] == "already-on-phase-branch"
            and git(work, "rev-parse", "HEAD")[1] == head,
            f"action={res2.get('action')}",
        )
    )

    # 3. A leftover branch from an earlier run of the same phase is resumed,
    #    not replaced, so the phase's own commits are not orphaned.
    (work / "d.txt").write_text("earlier run\n")
    git(work, "add", "d.txt")
    git(work, "commit", "-q", "-m", "earlier run of this phase")
    earlier = git(work, "rev-parse", "HEAD")[1]
    git(work, "checkout", "-q", "main")
    git(work, "push", "-q", "origin", "main")
    res3 = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a leftover phase branch is resumed, not recreated",
            res3["ok"]
            and res3["action"] == "reused-phase-branch"
            and current_branch(work) == branch
            and git(work, "rev-parse", "HEAD")[1] == earlier,
            f"action={res3.get('action')} head={res3.get('after')}",
        )
    )

    # 4. A checkout sitting on some other branch is a hard stop. Moving a
    #    working tree that holds unreviewed work between branches is the
    #    operation that can leave that work in pieces.
    git(work, "checkout", "-q", "-b", "someone-elses-work")
    res4 = ensure_phase_branch(work, branch, base="main")
    results.append(
        (
            "a checkout on another branch is never switched silently",
            (not res4["ok"])
            and "not the phase branch" in res4["blockers"][-1]
            and current_branch(work) == "someone-elses-work",
            f"blockers={res4.get('blockers')}",
        )
    )

    # 5. A missing integration branch stops rather than inventing a start point.
    git(work, "checkout", "-q", "main")
    res5 = ensure_phase_branch(work, "phase/100999-nothing-here", base="no-such-base")
    results.append(
        (
            "a missing integration branch refuses",
            (not res5["ok"]) and "does not exist" in res5["blockers"][-1],
            f"blockers={res5.get('blockers')}",
        )
    )

    # 6. A checkout that is reviewed on the integration branch rather than as a
    #    pull request is left alone. It must not be moved onto a phase branch,
    #    because nothing there is unreviewed code, and creating the branch for
    #    it would strand run state on a branch nobody merges. A name that does
    #    not exist yet, so the check can prove nothing was created.
    fresh = "phase/100440-never-created"
    res6 = ensure_phase_branch(work, fresh, base="main", integration_only=True)
    results.append(
        (
            "an integration-branch checkout is left where it is, and no "
            "branch is created for it",
            res6["ok"]
            and res6["action"] == "stays-on-integration-branch"
            and current_branch(work) == "main"
            and git(work, "rev-parse", "--verify", f"refs/heads/{fresh}")[0] != 0,
            f"branch={current_branch(work)} {res6.get('action')}",
        )
    )

    # 7. It is still fail-closed: a checkout sitting on some other branch is a
    #    stop, and a missing integration branch is a stop. Silence here would
    #    let run state be published from an unexpected place.
    git(work, "checkout", "-q", "-b", "unrelated-work")
    res7 = ensure_phase_branch(work, branch, base="main", integration_only=True)
    results.append(
        (
            "an integration-branch checkout on another branch refuses",
            (not res7["ok"])
            and "not 'main'" in res7["blockers"][-1]
            and current_branch(work) == "unrelated-work",
            f"blockers={res7.get('blockers')}",
        )
    )
    res8 = ensure_phase_branch(work, branch, base="no-such-base", integration_only=True)
    results.append(
        (
            "an integration-branch checkout with no base refuses",
            (not res8["ok"]) and "does not exist" in res8["blockers"][-1],
            f"blockers={res8.get('blockers')}",
        )
    )
    return results


def _phase_branch_name_checks(base):
    """The published branch name must not carry the phase number.

    The branch is pushed to the public repository, where the number is an
    internal label a reviewer should not have to read past. The behaviour part
    is what identifies the phase's roadmap file, so the number is still checked
    against the phase the caller means: that check now reads the file name,
    because the branch no longer carries the number at all.
    """
    results = []
    fixture = base / "branch-name"
    _configure_self_test(
        fixture, fixture / BOARD_REL, roadmap=Path(BOARD_REL) / "roadmap"
    )
    name = "phase-100440-backup-archive-format-and-directory.md"
    branch = phase_branch_for(100440, name)
    results.append(
        (
            "the branch carries the behaviour and no phase number",
            branch == "feature/backup-archive-format-and-directory"
            and "100440" not in branch,
            f"branch={branch}",
        )
    )

    # The wrong roadmap file must still be refused, or a phase could be launched
    # on another phase's branch.
    try:
        phase_branch_for(100841, name)
        wrong = None
    except ConfigError as exc:
        wrong = str(exc)
    results.append(
        (
            "a roadmap file for another phase is still refused",
            wrong is not None and "100440" in wrong and "100841" in wrong,
            f"error={wrong}",
        )
    )

    # The behaviour is the only thing separating two phases now, so a duplicate
    # must stop the launch: otherwise the second phase would resume the first
    # phase's branch and inherit its commits.
    root = fixture
    roadmap = root / BOARD_REL / "roadmap"
    roadmap.mkdir(parents=True)
    (roadmap / name).write_text("x\n")
    clash_name = "phase-100460-backup-archive-format-and-directory.md"
    (roadmap / clash_name).write_text("x\n")
    clash = assert_phase_branch_unique(root, 100440, name)
    results.append(
        (
            "a behaviour another phase also claims is refused",
            (not clash["ok"]) and clash_name in clash["blockers"][-1],
            f"blockers={clash.get('blockers')}",
        )
    )

    (roadmap / clash_name).unlink()
    unique = assert_phase_branch_unique(root, 100440, name)
    results.append(
        (
            "a behaviour only this phase claims is accepted",
            unique["ok"] and unique.get("action") == "phase-branch-name-unique",
            f"action={unique.get('action')}",
        )
    )

    # The folder the phases were chosen from is the folder that has to be
    # scanned. Reading the derived one instead would leave a collision inside a
    # configured folder unseen, which is the exact failure this check refuses.
    named = base / "branch-name-named"
    (named / BOARD_REL / "roadmap").mkdir(parents=True)
    elsewhere = named / "other-roadmap"
    elsewhere.mkdir()
    (elsewhere / name).write_text("x\n")
    (elsewhere / clash_name).write_text("x\n")
    same_name_only_in_derived = assert_phase_branch_unique(
        named, 100440, name, elsewhere
    )
    results.append(
        (
            "a behaviour another phase in the configured folder claims is refused",
            (not same_name_only_in_derived["ok"])
            and clash_name in same_name_only_in_derived["blockers"][-1],
            f"blockers={same_name_only_in_derived.get('blockers')}",
        )
    )
    relative = assert_phase_branch_unique(named, 100440, name, "other-roadmap")
    results.append(
        (
            "a relative configured folder resolves against the repository root",
            (not relative["ok"]) and clash_name in relative["blockers"][-1],
            f"blockers={relative.get('blockers')}",
        )
    )

    # And the mode itself, not only the helper it calls. The helper taking a
    # folder is worth nothing if the mode drops it on the way, and the mode is
    # the thing the driver runs on every launch. This fixture has no git
    # remotes, so the check is about the refusal the mode returns: a mode that
    # reached the uniqueness check at all refuses, and one that dropped the
    # folder would carry on to try to cut a branch in a directory that is not a
    # repository, which is a different failure.
    refused = mode_branch(named, 100440, name, "other-roadmap")
    results.append(
        (
            "the branch mode scans the configured folder and refuses the collision",
            (not refused["ok"])
            and refused.get("mode") == "branch"
            and clash_name in str(refused.get("repos")),
            f"ok={refused.get('ok')} blockers={refused.get('repos')}",
        )
    )
    return results


def _branch_pair_checks(base):
    """The public checkout gets the phase branch; the private one does not.

    Checks 1 to 8 above drive ``ensure_phase_branch`` one checkout at a time,
    so every one of them passes whichever checkout it is handed. None of them
    can see the decision this group covers: which of the two real checkouts is
    the one that gets branched. That decision was once written against the
    public path instead of the private one, and because both checkouts report
    ``ok`` either way, every single-repo check stayed green while a phase was
    launched onto the integration branch to write unreviewed code into it.
    """
    results = []
    pub, nested, _ = _publish_fixture(base, "branch-pair", 100809)
    # The shared fixture builds its throwaway pair on "main". Both real
    # checkouts are on "master", and that is the name the phase branch is cut
    # from, so the pair is put on "master" here too. Otherwise this group would
    # pass or fail over a branch name the pipeline never meets in production.
    for repo in (pub, nested):
        git(repo, "checkout", "-q", "-B", INTEGRATION_BRANCH)
        git(repo, "push", "-q", "-u", "origin", INTEGRATION_BRANCH)
    name = "phase-100809-backup-prune-retention.md"
    branch = phase_branch_for(100809, name)
    made = branch_phase_checkouts(pub, branch)
    by_repo = {Path(r["repo"]).resolve(): r for r in made}

    # The public checkout holds the product code, so it is the one that moves.
    public = by_repo[pub.resolve()]
    results.append(
        (
            "the public checkout is put on the phase branch",
            public["ok"]
            and current_branch(pub) == branch
            and git(pub, "rev-parse", "--verify", f"refs/heads/{branch}")[0] == 0,
            f"branch={current_branch(pub)} {public.get('action')}",
        )
    )

    # The private checkout holds run state, so it stays on the integration
    # branch and gets no phase branch of its own. A branch created here would
    # strand run state on a ref nobody merges.
    private = by_repo[nested.resolve()]
    results.append(
        (
            "the private checkout stays on the integration branch and "
            "no phase branch is created for it",
            private["ok"]
            and private["action"] == "stays-on-integration-branch"
            and current_branch(nested) == INTEGRATION_BRANCH
            and git(nested, "rev-parse", "--verify", f"refs/heads/{branch}")[0] != 0,
            f"branch={current_branch(nested)} {private.get('action')}",
        )
    )

    # The two must not be swapped. Assert the paths as well as the branches, so
    # a future swap cannot pass by leaving both checkouts on the same branch.
    results.append(
        (
            "the two checkouts are branched by role, not by accident",
            len(made) == 2
            and Path(made[0]["repo"]).resolve() == pub.resolve()
            and made[0]["action"] == "created-phase-branch"
            and made[1]["action"] == "stays-on-integration-branch",
            f"public={made[0].get('action')} private={made[1].get('action')}",
        )
    )

    # The receipt is what the driver logs, so it has to name where each
    # checkout ended up rather than the branch that was asked for. The driver
    # built its log line from the top-level "branch", which is one string
    # shared by both checkouts, and that is how a swapped pair logged as a
    # correct launch.
    results.append(
        (
            "the receipt reports where each checkout actually is",
            public.get("after_branch") == branch
            and private.get("after_branch") == INTEGRATION_BRANCH
            and public.get("after_branch") != private.get("after_branch"),
            f"public={public.get('after_branch')} "
            f"private={private.get('after_branch')}",
        )
    )

    # A refusal must say where the checkout was too, because a refusal is the
    # case where the operator reads this receipt to find out what to undo.
    git(nested, "checkout", "-q", "-b", "someones-unfinished-work")
    stuck = branch_phase_checkouts(pub, branch)
    stuck_private = {Path(r["repo"]).resolve(): r for r in stuck}[nested.resolve()]
    git(nested, "checkout", "-q", INTEGRATION_BRANCH)
    results.append(
        (
            "a refused checkout still reports the branch it is stuck on",
            (not stuck_private["ok"])
            and stuck_private.get("after_branch") == "someones-unfinished-work",
            f"ok={stuck_private.get('ok')} "
            f"after_branch={stuck_private.get('after_branch')}",
        )
    )
    return results


class _StubGH:
    """Stand in for ``gh`` so the merge check can be driven by return value.

    The pipeline calls ``gh`` as a subprocess, so the only seam available is the
    process itself. Replacing ``subprocess.run`` for the duration is what lets
    each answer be tested without a token and without a network, and it is
    scoped with ``with`` so a failing assertion cannot leave the stub installed
    for a later check.
    """

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, argv, **kwargs):
        import types

        # Only gh is stubbed. Everything else in this module runs git through
        # the same subprocess.run, so a stub that swallowed those would make
        # every git call under test fail, which is a failure in the harness
        # rather than a finding about the code.
        if not argv or Path(argv[0]).name != "gh":
            return self.saved(argv, **kwargs)
        self.calls.append(argv)
        if isinstance(self.result, Exception):
            raise self.result
        if callable(self.result):
            self.result = self.result(argv)
        return types.SimpleNamespace(
            returncode=self.result.get("returncode", 0),
            stdout=self.result.get("stdout", ""),
            stderr=self.result.get("stderr", ""),
        )

    def __enter__(self):
        self.saved = subprocess.run
        subprocess.run = self
        return self

    def __exit__(self, *_exc):
        subprocess.run = self.saved


def _gh_says(state, extra=""):
    """A ``gh pr view --json state`` response body."""
    return {
        "stdout": json.dumps(
            {
                "state": state,
                "mergedAt": extra or None,
                "mergeCommit": {"oid": "0" * 40},
            }
        )
    }


def _sequencing_checks(base):
    """Items 1, 2, 3, 5 and 7 of the reliable-sequencing plan.

    Every merge-detection case here is measured against a real squash, because
    the check this replaces passed a single-commit test and failed on a
    multi-commit one. That is why the fixtures commit several times rather than
    once: a one-commit phase is not the case that occurs.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # --- Item 2: the pull request reference is parsed at receipt time. ---
        parsed = [
            (
                "https://github.com/example-org/example-repo/pull/42",
                ("github.com", "example-org", "example-repo", 42),
            ),
            (
                "https://github.com/example-org/example-repo/pull/42/files",
                ("github.com", "example-org", "example-repo", 42),
            ),
            (
                "https://github.com/example-org/example-repo/pull/42#issuecomment-1",
                ("github.com", "example-org", "example-repo", 42),
            ),
            (
                "http://ghe.example.com/example-org/example-repo/pull/7",
                ("ghe.example.com", "example-org", "example-repo", 7),
            ),
        ]
        wrong = []
        for url, want in parsed:
            ref = pull_request_ref(url)
            got = (
                (ref["host"], ref["owner"], ref["repo"], ref["number"]) if ref else None
            )
            if got != want:
                wrong.append((url, got, want))
        # The negative direction matters more: a string that is not a pull
        # request URL must yield None so the check is skipped, never a number
        # guessed from the wrong field.
        for url in (
            "",
            "not a url",
            "https://github.com/example-org/example-repo/issues/42",
            "https://github.com/example-org/example-repo/pull/abc",
            None,
        ):
            if pull_request_ref(url) is not None:
                wrong.append((url, "parsed", None))
        results.append(
            (
                "a pull request URL yields owner, repo and number; anything else "
                "yields no reference",
                not wrong,
                f"mismatches={wrong}",
            )
        )

        # --- Item 1: three answers, and unknown is never merged. ---
        with _StubGH(_gh_says("MERGED", "2026-01-01T00:00:00Z")):
            state, detail = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                "gh reporting MERGED is the merged answer",
                state == "merged" and detail == "0" * 40,
                f"state={state} detail={detail}",
            )
        )
        with _StubGH(_gh_says("OPEN")):
            state, _ = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                "gh reporting OPEN is the not-merged answer",
                state == "not-merged",
                f"state={state}",
            )
        )
        # Exit 4 is gh's documented "authentication required", exit 1 a general
        # failure. Both must read unknown, or a token that cannot read pull
        # requests would look like a phase that was never merged... which is
        # the safe direction but silently disables the fix, so it is pinned.
        for code in (1, 4):
            with _StubGH({"returncode": code, "stderr": "account name here"}):
                state, detail = gh_pull_request_state(42, "example-org", "example-repo")
            results.append(
                (
                    f"gh exiting {code} is unknown, not a merge",
                    state == "unknown" and "account name" not in detail,
                    f"state={state} detail={detail}",
                )
            )
        with _StubGH(FileNotFoundError()):
            state, _ = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            ("gh missing is unknown, not a merge", state == "unknown", f"state={state}")
        )
        with _StubGH(subprocess.TimeoutExpired(cmd="gh", timeout=GH_TIMEOUT_S)):
            state, _ = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                "gh timing out is unknown, not a merge",
                state == "unknown",
                f"state={state}",
            )
        )
        with _StubGH({"stdout": "not json"}):
            state, _ = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                "unreadable gh output is unknown, not a merge",
                state == "unknown",
                f"state={state}",
            )
        )
        with _StubGH({"stdout": json.dumps({"state": "WHATEVER"})}):
            state, _ = gh_pull_request_state(42, "example-org", "example-repo")
        results.append(
            (
                "an unrecognised gh state is unknown, not a merge",
                state == "unknown",
                f"state={state}",
            )
        )

        # --- Item 1 end to end: a squash merge, recognised by GitHub. ---
        # Five commits, squashed into one, which is the case the commit-id test
        # alone fails. This is the deadlock the plan exists to remove.
        pub, nested, _run = _publish_fixture(base, "squash-github", 100451)
        branch_a = "feature/merge-detect-squash"
        ensure_phase_branch(pub, branch_a, base="main")
        for step in range(5):
            (pub / f"part-{step}.txt").write_text(f"phase work {step}\n")
            git(pub, "add", f"part-{step}.txt")
            git(pub, "commit", "-q", "-m", f"phase 100451 step {step}")
        res = publish_phase(
            pub,
            100451,
            pull_request="https://github.com/example-org/example-repo/pull/900",
        )
        ref = (res.get("receipt") or {}).get("pull_request_ref")
        results.append(
            (
                "the receipt records the parsed pull request alongside the URL",
                res.get("ok")
                and ref
                == {
                    "host": "github.com",
                    "owner": "example-org",
                    "repo": "example-repo",
                    "number": 900,
                },
                f"ref={ref}",
            )
        )
        # The human merges onto whichever branch this repository actually calls
        # its integration branch, read the same way the receipt read it. Naming
        # "main" here instead would merge into a branch the receipt never
        # mentions, and the check would refuse for the right reason and the
        # wrong one at the same time.
        integration = integration_branch(pub)
        git(pub, "checkout", "-q", integration)
        git(pub, "merge", "-q", "--squash", branch_a)
        git(pub, "commit", "-q", "-m", "phase 100451 (squashed by a human)")
        git(pub, "push", "-q", "origin", integration)
        private_integration = integration_branch(nested)
        git(nested, "checkout", "-q", private_integration)
        git(nested, "merge", "-q", "--no-edit", branch_a)
        git(nested, "push", "-q", "origin", private_integration)

        # The negative first: without GitHub the squash is not recognised, which
        # is the pre-existing behaviour and the reason the check exists.
        with _StubGH(_gh_says("OPEN")):
            blocked_open = mark_phase_published(pub, 100451)
        results.append(
            (
                "a squash merge with GitHub reporting OPEN stays blocked",
                (not blocked_open["ok"])
                and phase_receipt(pub, 100451).get("state") == "awaiting_review"
                and "not merged" in (blocked_open.get("blockers") or [""])[-1],
                f"blockers={blocked_open.get('blockers')}",
            )
        )
        with _StubGH({"returncode": 4}):
            blocked_unknown = mark_phase_published(pub, 100451)
        results.append(
            (
                "a squash merge with GitHub unreachable stays blocked rather than "
                "being assumed merged",
                (not blocked_unknown["ok"])
                and phase_receipt(pub, 100451).get("state") == "awaiting_review"
                and "could not be asked"
                in ((blocked_unknown.get("blockers") or [""])[-1]),
                f"blockers={blocked_unknown.get('blockers')}",
            )
        )
        with _StubGH(_gh_says("MERGED")):
            marked = mark_phase_published(pub, 100451)
        published = phase_receipt(pub, 100451)
        results.append(
            (
                "a squash merge is recorded once GitHub reports the pull request "
                "merged, and the receipt says which proof covered which checkout",
                marked["ok"]
                and marked.get("confirmed_by")
                == {"public": "github", "private": "commit-id"}
                and published.get("state") == "published"
                and published.get("merge_confirmed_by")
                == {"public": "github", "private": "commit-id"},
                f"ok={marked.get('ok')} by={marked.get('confirmed_by')} "
                f"receipt={published.get('merge_confirmed_by')}",
            )
        )

        # --- Item 1 fallback: a merge performed outside GitHub. ---
        pub2, nested2, _run2 = _publish_fixture(base, "squash-offsite", 100452)
        branch_b = "feature/merge-detect-offsite"
        ensure_phase_branch(pub2, branch_b, base="main")
        (pub2 / "part.txt").write_text("work\n")
        git(pub2, "add", "part.txt")
        git(pub2, "commit", "-q", "-m", "phase 100452")
        publish_phase(pub2, 100452)
        for repo in (pub2, nested2):
            merge_into = integration_branch(repo)
            git(repo, "checkout", "-q", merge_into)
            git(repo, "merge", "-q", "--no-edit", branch_b)
            git(repo, "push", "-q", "origin", merge_into)
        # gh is down, so only the commit-id test can answer. A merge commit
        # keeps the commit, so this must still succeed.
        with _StubGH({"returncode": 1}):
            offsite = mark_phase_published(pub2, 100452)
        offsite_receipt = phase_receipt(pub2, 100452)
        results.append(
            (
                "a merge commit is still recognised when GitHub cannot be asked",
                offsite["ok"]
                and offsite.get("confirmed_by")
                == {"public": "commit-id", "private": "commit-id"}
                and offsite_receipt.get("merge_confirmed_by")
                == {"public": "commit-id", "private": "commit-id"},
                f"ok={offsite.get('ok')} by={offsite.get('confirmed_by')} "
                f"receipt={offsite_receipt.get('merge_confirmed_by')}",
            )
        )

        # --- Item 5: the integration branch merged into the phase branch. ---
        d = base / "resync"
        d.mkdir()
        remote, work = _init_remote(d, "w")
        ensure_phase_branch(work, "feature/resync", base="main")
        (work / "phase.txt").write_text("the phase's work\n")
        git(work, "add", "phase.txt")
        git(work, "commit", "-q", "-m", "phase commit")
        before = git(work, "rev-parse", "HEAD")[1]
        noop = resync_integration(work, base="main")
        results.append(
            (
                "a resync with an unmoved integration branch does not move the phase "
                "branch",
                noop["ok"]
                and noop["action"] == "phase-ahead"
                and git(work, "rev-parse", "HEAD")[1] == before,
                f"action={noop.get('action')} relation={noop.get('relation')}",
            )
        )
        # Integration branch moves, disjoint paths: a plain merge, no rebase.
        _remote_commit(remote, d, "w")
        clean = resync_integration(work, base="main")
        parents = git(work, "rev-list", "--parents", "-n1", "HEAD")[1].split()
        results.append(
            (
                "a resync merges an advanced integration branch without rebasing",
                clean["ok"]
                and clean["action"] == "merged"
                and len(parents) == 3
                and (work / "phase.txt").is_file()
                and (work / "a.txt").is_file(),
                f"action={clean.get('action')} parents={parents}",
            )
        )
        # Now both sides own the same path with different content: a real
        # conflict, which must be left in place and named so an agent can
        # finish it rather than the pipeline cleaning it up.
        (work / "clash.txt").write_text("the phase's side\n")
        git(work, "add", "clash.txt")
        git(work, "commit", "-q", "-m", "phase takes a side on clash.txt")
        # A second throwaway clone, because `_remote_commit` only ever edits
        # a.txt and this case needs the integration branch to take the other
        # side on a path the phase already owns.
        other = d / "other-w2"
        subprocess.run(
            [git_binary(), "clone", "-q", str(remote), str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(other, "config", "user.email", "sync-test@example.com")
        git(other, "config", "user.name", "sync test")
        (other / "clash.txt").write_text("the integration branch's side\n")
        git(other, "add", "clash.txt")
        git(other, "commit", "-q", "-m", "integration takes a side")
        git(other, "push", "-q", "origin", "main")
        clash = resync_integration(work, base="main")
        results.append(
            (
                "a resync conflict is left in place with the paths named",
                (not clash["ok"])
                and clash.get("conflicted") == ["clash.txt"]
                and (work / ".git" / "MERGE_HEAD").exists()
                and "<<<<<<<" in (work / "clash.txt").read_text()
                and "git commit" in (clash.get("hint") or ""),
                f"action={clash.get('action')} "
                f"conflicted={clash.get('conflicted')} "
                f"blockers={clash.get('blockers')}",
            )
        )
        # The agent is expected to finish it; assert the recipe the finalize
        # prompt gives actually works, so the instruction is not folklore.
        (work / "clash.txt").write_text("resolved by the phase agent\n")
        git(work, "add", "clash.txt")
        rc_finish, _, err_finish = git(work, "commit", "--no-edit")
        results.append(
            (
                "the conflict the resync leaves can be finished the way the prompt "
                "instructs",
                rc_finish == 0 and not (work / ".git" / "MERGE_HEAD").exists(),
                f"rc={rc_finish} err={err_finish}",
            )
        )
        # The private checkout is always on the integration branch, so it is a
        # named no-op rather than a second integration.
        results.append(
            (
                "a checkout already on the integration branch is a named no-op",
                resync_integration(work, base="main")["action"]
                in {"already-on-integration-branch", "phase-ahead"},
                "reported, not dropped",
            )
        )

        # --- Item 3 and 7: a phase built on an open predecessor. ---
        pub3, nested3, _run3 = _publish_fixture(base, "stack-base", 100453)
        branch_first = "feature/stack-base-first"
        branch_second = "feature/stack-base-second"
        ensure_phase_branch(pub3, branch_first, base="main")
        (pub3 / "first.txt").write_text("the first phase's code\n")
        git(pub3, "add", "first.txt")
        git(pub3, "commit", "-q", "-m", "phase 100453: the first change")
        first = publish_phase(
            pub3,
            100453,
            pull_request="https://github.com/example-org/example-repo/pull/901",
        )
        results.append(
            (
                "a phase with no open predecessor publishes with no warning",
                first.get("ok") and "warning" not in first,
                f"warning={first.get('warning')}",
            )
        )
        predecessor = open_predecessor(pub3, 100454)
        results.append(
            (
                "an open predecessor is found for the next phase",
                (predecessor or {}).get("phase") == 100453
                and predecessor["branch"] == branch_first,
                f"predecessor={predecessor}",
            )
        )
        # The checkout is left on the predecessor's branch by publish_phase,
        # which is the state the driver is in when it branches the next phase,
        # so the fixture must not move it. ensure_phase_branch refuses to switch
        # a checkout that is on neither the phase branch nor its base, and that
        # refusal is correct here.
        made = branch_phase_checkouts(
            pub3, branch_second, base=(predecessor or {}).get("branch")
        )
        first_on_second = git(
            pub3, "rev-list", "--count", f"{branch_first}..{branch_second}"
        )[1].strip()
        results.append(
            (
                "the next phase branch is cut from the open predecessor, so it "
                "contains the predecessor's code",
                all(m["ok"] for m in made)
                and first_on_second == "0"
                and (pub3 / "first.txt").is_file(),
                f"commits ahead of predecessor={first_on_second} "
                f"results={[m.get('action') for m in made]} "
                f"blockers={[m.get('blockers') for m in made]}",
            )
        )
        # The private checkout stays on the integration branch even while the
        # public one is built on a predecessor. Asserted, because passing the
        # predecessor's base to it stops every launch and reads as a broken
        # pipeline rather than as a wiring mistake.
        private_actions = [m.get("action") for m in made[1:]]
        results.append(
            (
                "the private checkout is not moved onto the predecessor's branch",
                private_actions in ([], ["stays-on-integration-branch"])
                and current_branch(nested3) == "main",
                f"actions={private_actions} branch={current_branch(nested3)}",
            )
        )
        # Without the predecessor as base the phase would be built from the
        # integration branch, which does not have the code. Compared directly
        # rather than by creating a second branch, because the checkout is
        # deliberately still on the predecessor's branch here and moving it
        # would test the wrong thing.
        on_integration = git(pub3, "ls-tree", "--name-only", "main")[1].split()
        on_phase = git(pub3, "ls-tree", "--name-only", branch_second)[1].split()
        results.append(
            (
                "cutting from the integration branch instead would leave the "
                "predecessor's code behind",
                "first.txt" not in on_integration and "first.txt" in on_phase,
                f"integration={sorted(on_integration)} phase={sorted(on_phase)}",
            )
        )
        # Item 7: publishing on top of an unmerged predecessor warns. The second
        # phase needs its own reservation, because publication is fenced per
        # phase and that fence is the point of the helper. This runs while the
        # predecessor's branch still exists, because the receipt's record of
        # which commit it was stacked on is only obtainable then.
        from argus.phase_reservations import ReservationStore, save_reservation_file

        store3 = ReservationStore(pub3, branch="main")
        git(nested3, "fetch", "-q", "origin")
        git(nested3, "merge", "-q", "--ff-only", "origin/main")
        run_dir3 = nested3 / "runs" / "phase-100454"
        run_dir3.mkdir(parents=True, exist_ok=True)
        save_reservation_file(
            run_dir3 / "reservation.json", store3.reserve(100454, "server-01")
        )
        (run_dir3 / "run.json").write_text('{"state":"completed"}\n')
        (run_dir3 / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
        git(nested3, "add", "-A")
        git(nested3, "commit", "-q", "-m", "run state for phase 100454")
        git(nested3, "push", "-q", "origin", "main")
        git(pub3, "checkout", "-q", branch_second)
        second_res = publish_phase(
            pub3,
            100454,
            pull_request="https://github.com/example-org/example-repo/pull/902",
        )
        carries = second_res.get("carries_open_phase") or {}
        second_receipt = phase_receipt(pub3, 100454)
        stacked = second_receipt.get("stacked_on") or {}
        results.append(
            (
                "publishing on top of an unmerged predecessor warns that the pull "
                "request carries that work",
                second_res.get("ok")
                and carries.get("branch") == branch_first
                and "still waiting for review" in (second_res.get("warning") or ""),
                f"carries={carries} warning={second_res.get('warning')}",
            )
        )
        results.append(
            (
                "the receipt records the predecessor it was stacked on, with the "
                "commit, so a rewritten predecessor is detectable later",
                stacked.get("branch") == branch_first
                and stacked.get("phase") == "100453"
                and stacked.get("head") == git(pub3, "rev-parse", branch_first)[1],
                f"stacked_on={stacked}",
            )
        )

        # A predecessor naming a branch that is gone stops the launch rather
        # than quietly falling back to the integration branch. Checked through
        # the lookup the refusal uses, not through open_predecessor: that one
        # returns the *highest* open predecessor, which is now phase 100454, so
        # asking it about 100453 would prove nothing.
        git(pub3, "checkout", "-q", "main")
        git(pub3, "branch", "-D", branch_first)
        git(pub3, "push", "-q", "origin", "--delete", branch_first)
        still_named = awaiting_review(pub3, 100453)
        highest = open_predecessor(pub3, 100455)
        results.append(
            (
                "a predecessor whose branch is gone is still reported, so the "
                "launch step can refuse instead of falling back to the "
                "integration branch",
                (still_named or {}).get("branch") == branch_first
                and git(pub3, "rev-parse", "--verify", f"refs/heads/{branch_first}")[0]
                != 0
                and (highest or {}).get("branch") == branch_second,
                f"awaiting_review(100453)={still_named} "
                f"open_predecessor(100455)={highest}",
            )
        )
    except Exception as exc:  # noqa: BLE001 - the self-test reports the failure
        results.append(
            ("reliable-sequencing checks ran to completion", False, repr(exc))
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _tidy_and_dispatch_checks(base):
    """The mode dispatches, and the self-healing tidy that replaces the retry.

    These are the paths whose behaviour used to be untested, because the mode
    bodies lived inline in ``main`` where no self-test could reach them — and
    one of those paths is the difference between a recoverable hiccup and a
    pipeline that never starts another phase again.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        # --- mode_resync drives both checkouts through one dispatch. ---
        pub, nested, _run = _publish_fixture(base, "dispatch-resync", 100455)
        branch = "feature/dispatch-resync"
        ensure_phase_branch(pub, branch, base="main")
        (pub / "work.txt").write_text("the phase's work\n")
        git(pub, "add", "work.txt")
        git(pub, "commit", "-q", "-m", "phase 100455: the change")
        disp = mode_resync(pub)
        role_paths = {pub.resolve(): "public", nested.resolve(): "private"}
        roles = [
            role_paths.get(Path(r.get("repo", "")).resolve(), "unknown")
            for r in disp.get("repos", [])
        ]
        results.append(
            (
                "the resync dispatch reports both checkouts, and the private one "
                "as a named no-op rather than dropping it",
                disp["ok"]
                and len(roles) == 2
                and set(roles) == {"public", "private"}
                and any(
                    r.get("action")
                    in {"phase-ahead", "up-to-date", "already-on-integration-branch"}
                    for r in disp["repos"]
                    if Path(r.get("repo", "")).resolve() == nested.resolve()
                ),
                f"ok={disp.get('ok')} roles={roles} "
                f"actions={[r.get('action') for r in disp.get('repos', [])]}",
            )
        )

        # --- mode_branch refuses when the predecessor's branch is gone. ---
        # The refusal used to live inline in main() and had no check at all.
        pub2, nested2, _run2 = _publish_fixture(base, "dispatch-branch", 100456)
        first = "feature/dispatch-branch-first"
        ensure_phase_branch(pub2, first, base="main")
        (pub2 / "first.txt").write_text("the first phase's code\n")
        git(pub2, "add", "first.txt")
        git(pub2, "commit", "-q", "-m", "phase 100456: the first change")
        publish_phase(pub2, 100456)
        (nested2 / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested2 / "roadmap" / "phase-100457-dispatch-next.md").write_text("x\n")
        (nested2 / "roadmap" / "phase-100458-dispatch-after.md").write_text("x\n")
        git(nested2, "add", "-A")
        git(nested2, "commit", "-q", "-m", "roadmap for the next phases")
        git(nested2, "push", "-q", "origin", "main")
        # The branch comes from the roadmap file's behaviour, not the phase
        # number, so the test asks for the same branch the mode will create
        # rather than naming one and hoping they agree.
        second = phase_branch_for(100457, "phase-100457-dispatch-next.md")
        good = mode_branch(pub2, 100457, "phase-100457-dispatch-next")
        results.append(
            (
                "the branch dispatch cuts the next phase from the open predecessor",
                good["ok"]
                and good.get("predecessor", {}).get("branch") == first
                and "first.txt"
                in git(pub2, "ls-tree", "--name-only", second)[1].split(),
                f"ok={good.get('ok')} base={good.get('base')} branch={second} "
                f"tree={git(pub2, 'ls-tree', '--name-only', second)[1].split()}",
            )
        )
        # A predecessor that moves under the phase is reported. The existence
        # check cannot see this — a force-pushed branch still exists — so without
        # an explicit comparison a phase would silently be based on commits
        # nobody reviewed.
        recorded_head = git(pub2, "rev-parse", first)[1]
        (pub2 / "late.txt").write_text("added to the predecessor later\n")
        git(pub2, "checkout", "-q", first)
        git(pub2, "add", "late.txt")
        git(pub2, "commit", "-q", "-m", "a late commit on the predecessor")
        moved = mode_branch(pub2, 100457, "phase-100457-dispatch-next")
        drift = moved.get("predecessor_moved") or {}
        results.append(
            (
                "a predecessor that moved since the phase was based on it is "
                "reported, not silently accepted",
                moved["ok"]
                and drift.get("branch") == first
                and drift.get("recorded_head") == recorded_head
                and drift.get("current_head") == git(pub2, "rev-parse", first)[1]
                and "force-pushed predecessor" in (moved.get("warning") or ""),
                f"drift={drift} warning={moved.get('warning')}",
            )
        )
        # Now remove the predecessor's branch everywhere and try again.
        git(pub2, "checkout", "-q", "main")
        git(pub2, "branch", "-D", first)
        git(pub2, "push", "-q", "origin", "--delete", first)
        git(pub2, "checkout", "-q", second)
        git(pub2, "branch", "-D", second)
        refusal = mode_branch(pub2, 100458, "phase-100458-dispatch-after.md")
        results.append(
            (
                "the branch dispatch refuses when the predecessor's branch is gone, "
                "rather than falling back to the integration branch",
                (not refusal["ok"])
                and "does not exist in this checkout"
                in (refusal["repos"][0].get("blockers") or [""])[0]
                and git(pub2, "rev-parse", "--verify", f"refs/heads/{first}")[0] != 0
                and git(
                    pub2,
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{phase_branch_for(100458, 'phase-100458-dispatch-after.md')}",
                )[0]
                != 0,
                f"ok={refusal.get('ok')} "
                f"blockers={refusal.get('repos', [{}])[0].get('blockers')}",
            )
        )

        # --- The stall this whole step exists to remove. ---
        # A phase whose merge is confirmed but whose checkout cannot move:
        # the receipt says published, so nothing reports the phase as awaiting
        # review, so the old design retried nothing and the checkout stayed on a
        # dead branch for good. The tidy step is what makes it recoverable.
        pub3, nested3, _run3 = _publish_fixture(base, "dispatch-tidy", 100459)
        branch3 = "feature/dispatch-tidy"
        ensure_phase_branch(pub3, branch3, base="main")
        (pub3 / "phase.txt").write_text("the phase's change\n")
        git(pub3, "add", "phase.txt")
        git(pub3, "commit", "-q", "-m", "phase 100459: the change")
        publish_phase(pub3, 100459)
        base3 = integration_branch(pub3)
        for repo in (pub3, nested3):
            git(repo, "checkout", "-q", integration_branch(repo))
            git(repo, "merge", "-q", "--no-edit", branch3)
            git(repo, "push", "-q", "origin", integration_branch(repo))
            git(repo, "checkout", "-q", branch3)
        # One stray untracked file is enough to stop the branch switch.
        (pub3 / "stray-run-artifact.log").write_text("harness noise\n")
        stalled = mode_published(pub3, 100459)
        receipt3 = phase_receipt(pub3, 100459)
        results.append(
            (
                "a phase that is published but whose checkout cannot move reports "
                "the two outcomes separately instead of calling it still waiting",
                (not stalled["ok"])
                and stalled.get("state") == "PUBLISHED-TIDY-PENDING"
                and receipt3.get("state") == "published"
                and current_branch(pub3) == branch3
                and (pub3 / "stray-run-artifact.log").exists(),
                f"ok={stalled.get('ok')} state={stalled.get('state')} "
                f"branch={current_branch(pub3)}",
            )
        )
        # The next tick recovers, with nothing but the receipt to go on.
        (pub3 / "stray-run-artifact.log").unlink()
        tidied = mode_tidy(pub3)
        results.append(
            (
                "the checkout is recovered on a later tick from the receipt alone, "
                "so a published phase never leaves the pipeline stuck behind it",
                tidied["ok"]
                and current_branch(pub3) == base3
                and any(
                    r.get("action") == "returned-to-integration-branch"
                    for r in tidied.get("repos", [])
                ),
                f"ok={tidied.get('ok')} branch={current_branch(pub3)} "
                f"actions={[r.get('action') for r in tidied.get('repos', [])]}",
            )
        )
        # And it is idempotent, so a tick that runs it twice moves nothing.
        again = mode_tidy(pub3)
        results.append(
            (
                "tidying again is a no-op",
                again["ok"] and current_branch(pub3) == base3,
                f"branch={current_branch(pub3)}",
            )
        )

        # --- Tidy must not move a phase that is still waiting for a human. ---
        pub4, nested4, _run4 = _publish_fixture(base, "dispatch-untidy", 100460)
        branch4 = "feature/dispatch-untidy"
        ensure_phase_branch(pub4, branch4, base="main")
        (pub4 / "phase.txt").write_text("the phase's change\n")
        git(pub4, "add", "phase.txt")
        git(pub4, "commit", "-q", "-m", "phase 100460: the change")
        publish_phase(pub4, 100460)
        # Merged, so the checkout COULD move — but this is the control: a phase
        # whose receipt still says awaiting_review must never be moved off its
        # branch, because its code is not on the integration branch yet.
        untidied = tidy_published_checkout(pub4, base=integration_branch(pub4))
        results.append(
            (
                "tidy leaves a phase that is still awaiting review exactly where it "
                "is, even though it could be moved",
                untidied["ok"]
                and untidied["action"] == "phase-not-yet-published"
                and current_branch(pub4) == branch4,
                f"action={untidied.get('action')} branch={current_branch(pub4)}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            (
                "tidy-and-dispatch checks ran to completion",
                False,
                traceback.format_exc(),
            )
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _two_phase_stack_checks(base):
    """Two phases open at once, driven through every step the pipeline uses.

    Every other check here exercises one decision. This one walks the sequence
    the change actually produces — first phase published and waiting, second
    phase branched off it, the integration branch moving underneath both, second
    phase published carrying the first — and asserts the state at each step.

    It is a check rather than a driver run: the tmux window, the lock and the
    agent harness stay stubbed, but every git operation, every receipt and every
    refusal is the real code against real remotes.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        # 100469 is taken by the fixture itself; the two phases under test
        # are 100470 and 100471, so each needs its own reservation below.
        pub, nested, _run = _publish_fixture(base, "stack-e2e", 100469)
        store = ReservationStore(pub, branch="main")
        base_branch = integration_branch(pub)
        first_branch = phase_branch_for(100470, "phase-100470-stack-first.md")
        second_branch = phase_branch_for(100471, "phase-100471-stack-second.md")
        # 100472 and 100473 are the two phases the integration-branch launch is
        # checked against, one with a clean tree and one carrying uncommitted
        # work. They are past the two phases above so neither is a predecessor of
        # the other.
        third_roadmap = "phase-100472-evidence-freshness-sweep.md"

        def reserve(phase):
            git(nested, "fetch", "-q", "origin")
            git(nested, "merge", "-q", "--ff-only", "origin/main")
            rd = nested / "runs" / f"phase-{phase:06d}"
            rd.mkdir(parents=True, exist_ok=True)
            save_reservation_file(
                rd / "reservation.json", store.reserve(phase, "server-01")
            )
            (rd / "run.json").write_text('{"state":"completed"}\n')
            (rd / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
            git(nested, "add", "-A")
            git(nested, "commit", "-q", "-m", f"run state {phase}")
            git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one: branched from the integration branch, worked on,
        # published, waiting for a human.
        (nested / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested / "roadmap" / "phase-100470-stack-first.md").write_text("x\n")
        (nested / "roadmap" / "phase-100471-stack-second.md").write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "roadmap")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        one_branch = mode_branch(pub, 100470, "phase-100470-stack-first.md")
        results.append(
            (
                "with nothing open, a phase branches from the integration branch",
                one_branch["ok"]
                and one_branch.get("base") == base_branch
                and one_branch.get("predecessor") is None,
                f"ok={one_branch.get('ok')} base={one_branch.get('base')} "
                f"predecessor={one_branch.get('predecessor')}",
            )
        )
        (pub / "first.txt").write_text("phase one's code\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100470: the first change")
        reserve(100470)
        one = publish_phase(
            pub,
            100470,
            pull_request="https://github.com/example-org/example-repo/pull/970",
        )
        results.append(
            (
                "phase one publishes and waits, carrying no earlier phase",
                one["ok"]
                and one.get("state") == "AWAITING_REVIEW"
                and not one.get("carries_open_phase")
                and not one.get("warning"),
                f"ok={one.get('ok')} state={one.get('state')} "
                f"warning={one.get('warning')}",
            )
        )

        # Phase two starts while phase one is still open.
        two_branch = mode_branch(pub, 100471, "phase-100471-stack-second.md")
        results.append(
            (
                "phase two branches off phase one's open branch, not off the "
                "integration branch, and inherits its code",
                two_branch["ok"]
                and two_branch.get("base") == first_branch
                and "first.txt"
                in git(pub, "ls-tree", "--name-only", second_branch)[1].split(),
                f"ok={two_branch.get('ok')} base={two_branch.get('base')} "
                f"tree={sorted(git(pub, 'ls-tree', '--name-only', second_branch)[1].split())}",
            )
        )

        # The integration branch moves underneath the open phase, from another
        # machine: exactly the situation the resync exists for.
        other = base / "stack-e2e-other"
        remote_url = git(pub, "remote", "get-url", "origin")[1]
        subprocess.run(
            [git_binary(), "clone", "-q", remote_url, str(other)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(other, "config", "user.email", "e2e@example.com")
        git(other, "config", "user.name", "e2e")
        git(other, "checkout", "-q", base_branch)
        (other / "from-elsewhere.txt").write_text("someone else's commit\n")
        git(other, "add", "from-elsewhere.txt")
        git(other, "commit", "-q", "-m", "another machine lands a change")
        git(other, "push", "-q", "origin", base_branch)
        resynced = mode_resync(pub)
        public_resync = next(
            (
                r
                for r in resynced.get("repos", [])
                if Path(r.get("repo", "")).resolve() == pub.resolve()
            ),
            {},
        )
        results.append(
            (
                "a commit that landed on the integration branch while a phase was "
                "open is merged into that phase's branch",
                resynced["ok"]
                and public_resync.get("action") == "merged"
                and (pub / "from-elsewhere.txt").is_file()
                and (pub / "first.txt").is_file(),
                f"action={public_resync.get('action')} "
                f"actions={[r.get('action') for r in resynced.get('repos', [])]}",
            )
        )

        (pub / "second.txt").write_text("phase two's code\n")
        git(pub, "add", "second.txt")
        git(pub, "commit", "-q", "-m", "phase 100471: the second change")
        reserve(100471)
        two = publish_phase(
            pub,
            100471,
            pull_request="https://github.com/example-org/example-repo/pull/971",
        )
        carried = two.get("carries_open_phase") or {}
        results.append(
            (
                "phase two publishes carrying phase one's unmerged work, and says so",
                two["ok"]
                and carried.get("branch") == first_branch
                and "still waiting for review" in (two.get("warning") or ""),
                f"carries={carried} warning={two.get('warning')}",
            )
        )

        # The human squash-merges phase one, which is what GitHub's button does
        # by default and the case the commit-id check alone cannot see.
        git(pub, "checkout", "-q", base_branch)
        git(pub, "merge", "-q", "--squash", first_branch)
        git(pub, "commit", "-q", "-m", "phase 100470 (squashed by a human)")
        git(pub, "push", "-q", "origin", base_branch)
        with _StubGH(_gh_says("MERGED")):
            first_done = mode_published(pub, 100470)
        results.append(
            (
                "a squash-merged first phase resolves the check and tidies the "
                "checkout",
                first_done["ok"]
                and first_done.get("state") == "PUBLISHED"
                and phase_receipt(pub, 100470).get("state") == "published"
                and current_branch(pub) == base_branch,
                f"ok={first_done.get('ok')} state={first_done.get('state')} "
                f"branch={current_branch(pub)}",
            )
        )

        # Phase two is still open, still stacked, and the next phase's base is
        # still phase two rather than phase one.
        results.append(
            (
                "phase two stays open after phase one merges, and the next phase "
                "would stack on phase two",
                awaiting_review(pub, 100471) is not None
                and (open_predecessor(pub, 100472) or {}).get("branch")
                == second_branch,
                f"awaiting_review(100471)={awaiting_review(pub, 100471)} "
                f"next base={(open_predecessor(pub, 100472) or {}).get('branch')}",
            )
        )

        # The integration branch holds the checkout again. That is what the
        # workflow asks a person to do once a pull request is open, and what the
        # tidy step does once a phase is published, so it is the ordinary state
        # between two phases and not an edge case. The next phase still stacks on
        # phase two, so the launch has to move the checkout off the integration
        # branch by itself.
        #
        # The check above this one is the one that let the bug through: it
        # branched phase two while the checkout was still sitting on phase one's
        # branch, which is a state the pipeline only passes through. It never
        # asked what happens after something returns the checkout, so every fix
        # built on it passed the self-test and stopped on the real machine.
        (nested / "roadmap" / third_roadmap).write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "roadmap")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        third_branch = phase_branch_for(100472, third_roadmap)
        started_on_integration = current_branch(pub) == base_branch
        three = mode_branch(pub, 100472, third_roadmap)
        results.append(
            (
                "a phase launches onto an open predecessor's branch while the "
                "checkout sits on the integration branch, with no person moving it",
                started_on_integration
                and three["ok"]
                and three.get("base") == second_branch
                and current_branch(pub) == third_branch
                and "second.txt"
                in git(pub, "ls-tree", "--name-only", third_branch)[1].split(),
                f"started_on_integration={started_on_integration} "
                f"ok={three.get('ok')} base={three.get('base')} "
                f"branch={current_branch(pub)} summary={three.get('summary')}",
            )
        )

        # The same launch with uncommitted work on the integration branch is a
        # stop, not a switch. Cutting the branch from a different commit would
        # reinterpret that work against a tree it was never written for, which is
        # the one way this move can lose someone's changes.
        git(pub, "checkout", "-q", base_branch)
        (pub / "uncommitted.txt").write_text("work in progress\n")
        fourth_roadmap = "phase-100473-guard-the-guard.md"
        (nested / "roadmap" / fourth_roadmap).write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "roadmap")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        four = mode_branch(pub, 100473, fourth_roadmap)
        results.append(
            (
                "uncommitted work on the integration branch still stops the launch",
                (not four["ok"])
                and current_branch(pub) == base_branch
                and (pub / "uncommitted.txt").read_text() == "work in progress\n"
                and git(
                    pub,
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{phase_branch_for(100473, fourth_roadmap)}",
                )[0]
                != 0,
                f"ok={four.get('ok')} branch={current_branch(pub)} "
                f"summary={four.get('summary')}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("two-phase stack checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _receipt_carry_checks(base):
    """A merged phase's receipt must not block the next phase's publication.

    The incident this reproduces: phases 100874 and 100875 were merged while
    phase 100876 was running. The merge check marked them published, which
    rewrote their receipts but never committed them, and phase 100876's finalize
    then refused with ``working tree is dirty after phase cleanup`` and halted
    the line.

    This is the end-to-end shape: publish and merge phase one, mark it
    published, then publish a *different* phase and require it to succeed. The
    single-assertion version lives in ``_merge_gate_checks``; this one proves
    the cross-phase consequence, which no other check constructs.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        # 100490 is taken by the fixture itself, so the second phase under test
        # is 100491 and needs its own reservation below.
        pub, nested, _run = _publish_fixture(base, "receipt-carry", 100490)
        store = ReservationStore(pub, branch="main")
        base_branch = integration_branch(pub)
        first_branch = phase_branch_for(100490, "phase-100490-carry-first.md")
        second_branch = phase_branch_for(100491, "phase-100491-carry-second.md")

        def reserve(phase):
            git(nested, "fetch", "-q", "origin")
            git(nested, "merge", "-q", "--ff-only", "origin/main")
            rd = nested / "runs" / f"phase-{phase:06d}"
            rd.mkdir(parents=True, exist_ok=True)
            save_reservation_file(
                rd / "reservation.json", store.reserve(phase, "server-01")
            )
            (rd / "run.json").write_text('{"state":"completed"}\n')
            (rd / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
            # Phase-scoped, never `add -A`: a blanket add would sweep up the
            # merged phase's receipt and hide the very dirt this check is for.
            git(nested, "add", f"runs/phase-{phase:06d}")
            git(nested, "commit", "-q", "-m", f"run state {phase}")
            git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one: on its own branch, published, waiting for a human. The
        # fixture already reserved 100490 and wrote its run state, so it needs
        # no extra reservation here.
        ensure_phase_branch(pub, first_branch, base=base_branch)
        (pub / "first.txt").write_text("phase one\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100490: the first change")
        one = publish_phase(
            pub,
            100490,
            pull_request="https://github.com/example-org/example-repo/pull/990",
        )
        results.append(
            (
                "the first phase publishes and waits for review",
                one["ok"] and one.get("state") == "AWAITING_REVIEW",
                f"ok={one.get('ok')} state={one.get('state')} "
                f"summary={one.get('summary')}",
            )
        )

        # The human squash-merges phase one, and the merge check records it.
        integration = integration_branch(pub)
        git(pub, "checkout", "-q", integration)
        git(pub, "merge", "-q", "--squash", first_branch)
        git(pub, "commit", "-q", "-m", "phase 100490 (squashed by a human)")
        git(pub, "push", "-q", "origin", integration)
        git(nested, "checkout", "-q", integration_branch(nested))
        git(nested, "merge", "-q", "--no-edit", first_branch)
        git(nested, "push", "-q", "origin", integration_branch(nested))
        with _StubGH(_gh_says("MERGED")):
            done = mode_published(pub, 100490)
        results.append(
            (
                "the merge check marks the merged phase published",
                done["ok"]
                and done.get("state") == "PUBLISHED"
                and phase_receipt(pub, 100490).get("state") == "published",
                f"ok={done.get('ok')} state={done.get('state')} "
                f"summary={done.get('summary')}",
            )
        )

        # The next phase publishes. Before the fix the merged phase's
        # uncommitted receipt was still dirt here, so this refused with
        # "working tree is dirty after phase cleanup" and the line halted.
        ensure_phase_branch(pub, second_branch, base=base_branch)
        (pub / "second.txt").write_text("phase two\n")
        git(pub, "add", "second.txt")
        git(pub, "commit", "-q", "-m", "phase 100491: the second change")
        reserve(100491)
        two = publish_phase(
            pub,
            100491,
            pull_request="https://github.com/example-org/example-repo/pull/991",
        )
        blocked_dirty = "working tree is dirty after phase cleanup" in str(two)
        results.append(
            (
                "a different phase publishes after a merged phase is marked published",
                two["ok"]
                and two.get("state") == "AWAITING_REVIEW"
                and not blocked_dirty,
                f"ok={two.get('ok')} state={two.get('state')} "
                f"blocked_dirty={blocked_dirty} error={two.get('error')} "
                f"dirty={two.get('dirty')}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("receipt carry checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _merge_gate_checks(base):
    """A receipt may only say published once a real merge put it there."""
    results = []
    pub, nested, run_dir = _publish_fixture(base, "merge-gate", 100446)
    branch = phase_branch_for(100446, "phase-100446-merge-gate.md")
    old = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        for repo in (pub, nested):
            ensure_phase_branch(repo, branch, base="main")
            # A real commit on the phase branch, in each checkout. Without one
            # the branch head equals the integration branch head, so the gate
            # would pass trivially and prove nothing.
            marker_file = "phase-work.txt"
            (repo / marker_file).write_text("the phase's product change\n")
            git(repo, "add", marker_file)
            git(repo, "commit", "-q", "-m", "phase 100446: the change")
        res = publish_phase(pub, 100446, pull_request="https://example/pr/1")
        receipt = res.get("receipt", {})
        # 1. finalize's receipt waits for a human and names the pull request.
        results.append(
            (
                "finalize records the phase as waiting for review",
                res["ok"]
                and res.get("state") == "AWAITING_REVIEW"
                and receipt.get("state") == "awaiting_review"
                and receipt.get("branch") == branch
                and receipt.get("pull_request") == "https://example/pr/1",
                f"state={res.get('state')} receipt={receipt}",
            )
        )

        # 2. Nothing is on the integration branch yet, so completion is refused.
        res2 = mark_phase_published(pub, 100446)
        results.append(
            (
                "completion is refused before the pull request merges",
                (not res2["ok"])
                and "not on the integration branch" in res2["blockers"][-1]
                and phase_receipt(pub, 100446).get("state") == "awaiting_review",
                f"blockers={res2.get('blockers')} "
                f"heads={res2.get('public_head') or ''}",
            )
        )

        # 3. A human merges. Now the same call succeeds and the board can
        #    record the phase, which is the first moment it is finished. Merged
        #    into the integration branch the receipt recorded, which is the
        #    branch mark_phase_published will look for.
        for repo in (pub, nested):
            merge_into = integration_branch(repo)
            git(repo, "checkout", "-q", merge_into)
            git(repo, "merge", "-q", "--no-edit", branch)
            git(repo, "push", "-q", "origin", merge_into)
            git(repo, "checkout", "-q", branch)
        res3 = mark_phase_published(pub, 100446)
        published = phase_receipt(pub, 100446)
        results.append(
            (
                "completion is recorded once the merge is real",
                res3["ok"]
                and res3["action"] == "marked-published"
                and published.get("state") == "published"
                and bool(published.get("public_commit"))
                and bool(published.get("private_commit"))
                and awaiting_review(pub, 100446) is None,
                f"blockers={res3.get('blockers')} "
                f"state={published.get('state')} "
                f"public={published.get('public_commit')} "
                f"private={published.get('private_commit')}",
            )
        )

        # 3b. The rewrite must leave the private tree clean. Before the fix the
        #     receipt was written and never committed, so it stayed dirt, and
        #     the next phase's publish refused over it and the driver halted.
        #     This is the smallest reproduction of the phase-100876 incident.
        nested_dirty = dirty_paths(nested)
        receipt_tracked = (
            "runs/phase-100446/publication.json"
            in git(nested, "ls-files", "runs")[1].splitlines()
        )
        results.append(
            (
                "marking a merged phase published leaves the private tree clean "
                "and the receipt committed",
                nested_dirty == [] and receipt_tracked,
                f"dirty={nested_dirty} receipt_tracked={receipt_tracked}",
            )
        )

        # 4. It is idempotent, and it never merges anything itself.
        head_before = git(pub, "rev-parse", "HEAD")[1]
        res4 = mark_phase_published(pub, 100446)
        results.append(
            (
                "recording a merge twice is a no-op and moves nothing",
                res4["ok"]
                and res4["action"] == "already-published"
                and git(pub, "rev-parse", "HEAD")[1] == head_before
                and current_branch(pub) == branch,
                f"action={res4.get('action')}",
            )
        )

        # 5. The public checkout is still on the phase branch. Nothing in the
        #    pipeline leaves it, so the branch after branch is where the
        #    published mode is supposed to clean up.
        results.append(
            (
                "the merged phase leaves the public checkout on its branch",
                current_branch(pub) == branch,
                f"branch={current_branch(pub)}",
            )
        )

        # 6. Returning to the integration branch moves it and is a no-op twice.
        res6 = return_to_integration(pub, base="main")
        results.append(
            (
                "the public checkout returns to the integration branch",
                res6["ok"]
                and res6["action"] == "returned-to-integration-branch"
                and current_branch(pub) == "main"
                and res6.get("before") == branch,
                f"branch={current_branch(pub)} {res6.get('action')}",
            )
        )
        res6b = return_to_integration(pub, base="main")
        results.append(
            (
                "returning again is a no-op",
                res6b["ok"]
                and res6b["action"] == "already-on-integration-branch"
                and current_branch(pub) == "main",
                f"branch={current_branch(pub)} {res6b.get('action')}",
            )
        )

        # 7. A dirty tree is the one case where switching branches could lose
        #    work, so it must stop rather than carry the change across.
        (pub / "uncommitted.txt").write_text("work in progress\n")
        res7 = return_to_integration(pub, base="main")
        results.append(
            (
                "a dirty working tree refuses to switch branch",
                (not res7["ok"])
                and "uncommitted changes" in res7["blockers"][-1]
                and current_branch(pub) == "main"
                and (pub / "uncommitted.txt").exists(),
                f"branch={current_branch(pub)} blockers={res7.get('blockers')}",
            )
        )
        # Untracked, so removing the file is the whole cleanup. `git rm` would
        # stage the removal and leave the tree just as dirty for the next check.
        (pub / "uncommitted.txt").unlink()

        # 8. A missing integration branch stops rather than moving nowhere.
        res8 = return_to_integration(pub, base="no-such-base")
        results.append(
            (
                "a missing integration branch refuses to switch to",
                (not res8["ok"])
                and "does not exist" in res8["blockers"][-1]
                and current_branch(pub) == "main",
                f"branch={current_branch(pub)} blockers={res8.get('blockers')}",
            )
        )
    finally:
        conventions.override({"board.branch": old})
    return results


def _stale_receipt_checks(base):
    """A merged phase whose receipt still says awaiting_review must not block.

    This is the state the line actually stalls in, and no other check here
    builds it. Every other fixture for a phase that published also leaves the
    merge check to resolve it, so the one state that deadlocks the line — a
    phase whose run finished, whose pull request a human squash-merged, and
    whose receipt nobody has updated — is a state the suite never constructed.

    Two things made it unconstructible, and both are the bug rather than the
    fixture's fault:

    - The merge check was only ever told about phases the selector had *not*
      finished, so a phase that ran to completion was never checked, and its
      receipt stayed ``awaiting_review`` for ever.
    - The receipt's parsed pull request reference was written only at publish
      time, so a receipt published before that field existed could never be
      confirmed by GitHub at all. Its only remaining proof was the commit-id
      test, which a squash merge has already deleted.

    So the fixture here is a finished run plus a squash-merged pull request
    plus a receipt with no parsed reference: the real shape, built from the
    real entry points. The phase after it must branch from the integration
    branch, not from a branch whose content is already there.
    """
    results = []
    old_branch = conventions.setting("board.branch")
    conventions.override({"board.branch": "main"})
    try:
        from argus.phase_reservations import ReservationStore, save_reservation_file

        # The fixture takes 100484 for its own coordination board, so the two
        # phases under test are 100485 and 100486.
        pub, nested, _run = _publish_fixture(base, "stale-receipt", 100484)
        store = ReservationStore(pub, branch="main")
        base_branch = integration_branch(pub)
        first = phase_branch_for(100485, "phase-100485-stale-receipt.md")
        second = phase_branch_for(100486, "phase-100486-after-stale.md")

        (nested / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested / "roadmap" / "phase-100485-stale-receipt.md").write_text("x\n")
        (nested / "roadmap" / "phase-100486-after-stale.md").write_text("x\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "roadmap")
        git(nested, "push", "-q", "origin", integration_branch(nested))

        # Phase one runs to completion and publishes. The run finishing is what
        # makes the selector treat it as done, so this is the state that has to
        # be built before the merge, not after.
        mode_branch(pub, 100485, "phase-100485-stale-receipt.md")
        (pub / "first.txt").write_text("phase one\n")
        git(pub, "add", "first.txt")
        git(pub, "commit", "-q", "-m", "phase 100485: the first change")
        rd = nested / "runs" / "phase-100485"
        rd.mkdir(parents=True, exist_ok=True)
        save_reservation_file(
            rd / "reservation.json", store.reserve(100485, "server-01")
        )
        (rd / "run.json").write_text('{"state":"completed"}\n')
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "run state 100485")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        one = publish_phase(
            pub,
            100485,
            pull_request="https://github.com/example-org/example-repo/pull/980",
        )
        results.append(
            (
                "the phase under test published and is waiting for a human",
                one["ok"]
                and one.get("state") == "AWAITING_REVIEW"
                and phase_receipt(pub, 100485).get("state") == "awaiting_review",
                f"ok={one.get('ok')} state={one.get('state')}",
            )
        )

        # The human squash-merges it. The commit the phase pushed is gone from
        # history, which is precisely why the commit-id test cannot see this.
        git(pub, "checkout", "-q", base_branch)
        git(pub, "merge", "-q", "--squash", first)
        git(pub, "commit", "-q", "-m", "phase 100485 (squashed by a human)")
        git(pub, "push", "-q", "origin", base_branch)
        results.append(
            (
                "the squash really did rewrite history, so the commit-id test alone "
                "cannot see the merge",
                not _head_carried_locally(
                    pub, phase_receipt(pub, 100485)["public_head"], (base_branch,)
                )
                and (pub / "first.txt").is_file(),
                "the phase head is no longer an ancestor of the integration branch",
            )
        )

        # A receipt written before the reference was recorded. This is what the
        # pipeline's own older receipts look like, and it is the case where
        # GitHub is never asked because there is no number to ask about.
        receipt_path = rd / "publication.json"
        stale = json.loads(receipt_path.read_text())
        stale.pop("pull_request_ref", None)
        stale["pull_request"] = "https://github.com/example-org/example-repo/pull/980"
        receipt_path.write_text(json.dumps(stale, indent=2, sort_keys=True) + "\n")
        git(nested, "add", "-A")
        git(nested, "commit", "-q", "-m", "receipt without a parsed reference")
        git(nested, "push", "-q", "origin", integration_branch(nested))
        results.append(
            (
                "the receipt under test carries no parsed reference, so the old "
                "code never asks GitHub about it",
                (phase_receipt(pub, 100485) or {}).get("pull_request_ref") is None
                and bool((phase_receipt(pub, 100485) or {}).get("pull_request")),
                f"ref={(phase_receipt(pub, 100485) or {}).get('pull_request_ref')}",
            )
        )

        # GitHub knows the merge. The number is in the URL and nowhere else.
        with _StubGH(_gh_says("MERGED", "2026-06-01T00:00:00Z")) as gh:
            done = mode_published(pub, 100485)
        results.append(
            (
                "a merged phase with a stale receipt is marked published even "
                "though its receipt recorded no pull request reference",
                done["ok"]
                and done.get("state") == "PUBLISHED"
                and phase_receipt(pub, 100485).get("state") == "published",
                f"ok={done.get('ok')} state={done.get('state')} "
                f"gh_calls={len(gh.calls)} "
                f"blockers={[b for r in done.get('repos', []) for b in r.get('blockers', [])]}",
            )
        )
        results.append(
            (
                "GitHub was asked about the pull request named only in the URL",
                any("980" in " ".join(str(a) for a in call) for call in gh.calls),
                f"calls={gh.calls}",
            )
        )

        # The point of the whole exercise: the next phase is no longer stacked
        # on a branch whose content is already on the integration branch.
        results.append(
            (
                "after the merge is recorded, the next phase is not stacked on the "
                "merged phase's branch",
                open_predecessor(pub, 100486) is None,
                f"predecessor={open_predecessor(pub, 100486)}",
            )
        )
        two = mode_branch(pub, 100486, "phase-100486-after-stale.md")
        results.append(
            (
                "the next phase branches from the integration branch and inherits "
                "the merged phase's code",
                two["ok"]
                and two.get("base") == base_branch
                and "first.txt"
                in git(pub, "ls-tree", "--name-only", second)[1].split(),
                f"ok={two.get('ok')} base={two.get('base')} "
                f"predecessor={two.get('predecessor')}",
            )
        )

        # The harder case, and the one that actually stalled the line: GitHub
        # cannot be asked at all. The receipt stays stale, because only GitHub or
        # the pull request's own commit may write "published". The next phase
        # must still branch from master rather than from a dead branch, because
        # the code it needs is already there. This is the content proof, and it
        # is the reason the line recovers on a host with no working gh.
        # The fixture takes 100612 for its own coordination board, so the two
        # phases under test here are 100613 and 100614.
        pub3, nested3, _run3 = _publish_fixture(base, "stale-no-gh", 100612)
        base3 = integration_branch(pub3)
        branch3 = phase_branch_for(100613, "phase-100613-stale-no-gh.md")
        next3 = phase_branch_for(100614, "phase-100614-after-no-gh.md")
        (nested3 / "roadmap").mkdir(parents=True, exist_ok=True)
        (nested3 / "roadmap" / "phase-100613-stale-no-gh.md").write_text("x\n")
        (nested3 / "roadmap" / "phase-100614-after-no-gh.md").write_text("x\n")
        git(nested3, "add", "-A")
        git(nested3, "commit", "-q", "-m", "roadmap")
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        mode_branch(pub3, 100613, "phase-100613-stale-no-gh.md")
        (pub3 / "landed.txt").write_text("already on master\n")
        git(pub3, "add", "landed.txt")
        git(pub3, "commit", "-q", "-m", "phase 100613: the change")
        rd3 = nested3 / "runs" / "phase-100613"
        rd3.mkdir(parents=True, exist_ok=True)
        # This fixture has its own remotes, so it needs its own board. The store
        # built for the first fixture points at different checkouts, and a
        # reservation written through it lands nowhere this phase can publish
        # from — which shows up as a publish that silently writes no receipt.
        store3 = ReservationStore(pub3, branch="main")
        save_reservation_file(
            rd3 / "reservation.json", store3.reserve(100613, "server-01")
        )
        (rd3 / "run.json").write_text('{"state":"completed"}\n')
        git(nested3, "add", "-A")
        git(nested3, "commit", "-q", "-m", "run state 100613")
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        publish_phase(
            pub3,
            100613,
            pull_request="https://github.com/example-org/example-repo/pull/981",
        )
        git(pub3, "checkout", "-q", base3)
        git(pub3, "merge", "-q", "--squash", branch3)
        git(pub3, "commit", "-q", "-m", "phase 100613 (squashed)")
        git(pub3, "push", "-q", "origin", base3)
        # The private side is merged with a real merge commit, the way the
        # pipeline pushes it. Only the public side is squashed, because that is
        # the side whose proof is in question here.
        git(nested3, "checkout", "-q", integration_branch(nested3))
        git(nested3, "merge", "-q", "--no-edit", branch3)
        git(nested3, "push", "-q", "origin", integration_branch(nested3))
        with _StubGH({"returncode": 4}):
            nogh = mode_published(pub3, 100613)
        results.append(
            (
                "with GitHub unreachable the receipt is not rewritten, because "
                "only GitHub or the pull request's own commit may say merged",
                (not nogh["ok"])
                and (phase_receipt(pub3, 100613) or {}).get("state")
                == "awaiting_review",
                f"ok={nogh.get('ok')} "
                f"state={(phase_receipt(pub3, 100613) or {}).get('state')}",
            )
        )
        results.append(
            (
                "with GitHub unreachable the next phase still branches from the "
                "integration branch, because the code is already on it",
                open_predecessor(pub3, 100614) is None,
                f"predecessor={open_predecessor(pub3, 100614)}",
            )
        )
        three = mode_branch(pub3, 100614, "phase-100614-after-no-gh.md")
        results.append(
            (
                "a phase launched on a host that cannot ask GitHub inherits the "
                "merged phase's code instead of refusing to launch",
                three["ok"]
                and three.get("base") == base3
                and "landed.txt"
                in git(pub3, "ls-tree", "--name-only", next3)[1].split(),
                f"ok={three.get('ok')} base={three.get('base')} "
                f"blockers={[b for r in three.get('repos', []) for b in r.get('blockers', [])]}",
            )
        )
    except Exception:  # noqa: BLE001 - the self-test reports the failure
        import traceback

        results.append(
            ("stale-receipt checks ran to completion", False, traceback.format_exc())
        )
    finally:
        conventions.override({"board.branch": old_branch})
    return results


def _tree_proof_checks(base):
    """The tree a phase published must be findable on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see the merge. What survives is the content: the squash commit's
    tree is the phase branch's tree. That is the proof this checks, and it is
    the one that still works when GitHub cannot be asked at all.
    """
    results = []
    pub, _nested, _run = _publish_fixture(base, "tree-proof", 100482)
    integration = integration_branch(pub)
    branch = phase_branch_for(100482, "phase-100482-tree-proof.md")
    ensure_phase_branch(pub, branch, base=integration)
    for step in range(3):
        (pub / f"part-{step}.txt").write_text(f"work {step}\n")
        git(pub, "add", f"part-{step}.txt")
        git(pub, "commit", "-q", "-m", f"phase 100482 step {step}")
    head = git(pub, "rev-parse", "HEAD")[1]
    git(pub, "checkout", "-q", integration)
    git(pub, "merge", "-q", "--squash", branch)
    git(pub, "commit", "-q", "-m", "phase 100482 (squashed)")
    results.append(
        (
            "a squash-merged phase's content is on the integration branch even "
            "though its commit is not",
            _head_carried_locally(pub, head, (integration,)) is None
            and head_tree_on_integration(pub, head, integration),
            f"head={head[:12]}",
        )
    )

    # The negative direction: a tree that was never merged must not be found.
    other = phase_branch_for(100483, "phase-100483-unmerged.md")
    ensure_phase_branch(pub, other, base=integration)
    (pub / "unmerged.txt").write_text("still open\n")
    git(pub, "add", "unmerged.txt")
    git(pub, "commit", "-q", "-m", "phase 100483: not merged")
    unmerged_head = git(pub, "rev-parse", "HEAD")[1]
    git(pub, "checkout", "-q", integration)
    results.append(
        (
            "content that was never merged is not found on the integration branch",
            not head_tree_on_integration(pub, unmerged_head, integration),
            f"head={unmerged_head[:12]}",
        )
    )
    return results


def self_test_checks():
    """Run checks with explicit temporary layout settings."""
    conventions.configure(
        {
            "repo-root": Path(),
            "pipeline": None,
            "roadmap-dir": BOARD_REL / "roadmap",
            "driver.runs": BOARD_REL / "runs",
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
            "machine.id": "self-test",
            "commands.git": "git",
            "commands.gh": "gh",
            "board.repo": BOARD_REL,
            "board.file": Path("claims.json"),
            "board.branch": "main",
            "board.remote": "origin",
        }
    )
    return _self_test_checks()


def _self_test_checks():
    """Stubbed-remote checks. Pure: temp dirs only, no repo state touched."""
    checks = []
    base = Path(tempfile.mkdtemp(prefix="gitsync-selftest-"))

    # 1. stale start is synced by fast-forward, preserving the remote work.
    d = base / "stale"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: stale start fast-forwards",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and res["relation"] == "behind"
        and (work / "a.txt").read_text() == "two\n",
        f"action={res.get('action')} relation={res.get('relation')}",
    )

    # 2. diverged start is merged cleanly, accepting remote changes while
    # keeping every local commit.
    d = base / "diverged"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    head_before = git(work, "rev-parse", "HEAD")[1]
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    parents = git(work, "rev-list", "--parents", "-n1", "HEAD")[1].split()
    _check(
        checks,
        "sync: diverged start merges and keeps local work",
        res["ok"]
        and res["action"] == "merged"
        and res["relation"] == "diverged"
        and git(work, "rev-parse", "HEAD^1")[1] == head_before
        and (work / "b.txt").is_file()
        and (work / "a.txt").read_text() == "two\n"
        and len(parents) == 3,
        f"action={res.get('action')} parents={parents}",
    )

    # 2b. a conflicting divergence is aborted and halts, preserving local work.
    d = base / "conflict"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "a.txt").write_text("local change\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "local change")
    _remote_commit(remote, d, "w", content="remote change\n")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: conflicting start aborts and preserves local work",
        (not res["ok"])
        and res.get("conflicted") == ["a.txt"]
        and (work / "a.txt").read_text() == "local change\n"
        and not (work / ".git" / "MERGE_HEAD").exists(),
        f"conflicted={res.get('conflicted')}",
    )

    # 2c. local commits ahead of the remote do not block a start; the remote
    # has nothing new and the commits publish at finalize.
    d = base / "ahead"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: ahead start proceeds (nothing to pull)",
        res["ok"] and res["action"] == "ahead" and (work / "b.txt").is_file(),
        f"action={res.get('action')}",
    )

    # 3. clean push verifies the remote is an ancestor of local HEAD.
    d = base / "push-ok"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    res = sync_one(work, "push")
    _check(
        checks,
        "sync: clean final push verifies fast-forward",
        res["ok"] and res["action"] == "verified" and res["relation"] == "ahead",
        f"action={res.get('action')}",
    )

    # 4. diverged final push merges the remote in, then push fast-forwards.
    d = base / "push-diverged"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    _remote_commit(remote, d, "w")
    res = sync_one(work, "push")
    _check(
        checks,
        "sync: diverged final push merges before pushing",
        res["ok"]
        and res["action"] == "merged"
        and (work / "a.txt").read_text() == "two\n"
        and (work / "b.txt").is_file(),
        f"action={res.get('action')}",
    )

    # 4b. a conflicting final push leaves the conflict in place so the
    # finalize agent can resolve it (start mode aborts; push mode does not).
    d = base / "push-conflict"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "a.txt").write_text("local change\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "local change")
    _remote_commit(remote, d, "w", content="remote change\n")
    res = sync_one(work, "push")
    _check(
        checks,
        "sync: conflicting final push leaves the merge to resolve",
        (not res["ok"])
        and res.get("conflicted") == ["a.txt"]
        and "<<<<<<<" in (work / "a.txt").read_text()
        and (work / ".git" / "MERGE_HEAD").exists(),
        f"conflicted={res.get('conflicted')}",
    )

    # 5. a remote that is ahead of local is fast-forwarded before a push.
    d = base / "push-behind"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _remote_commit(remote, d, "w")
    res = sync_one(work, "push")
    _check(
        checks,
        "sync: behind final push fast-forwards",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and (work / "a.txt").read_text() == "two\n",
        f"action={res.get('action')} relation={res.get('relation')}",
    )

    # 6. overlapping uncommitted edits block a fast-forward and survive it.
    d = base / "dirty"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _remote_commit(remote, d, "w")
    (work / "a.txt").write_text("local edit\n")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: overlapping dirty edit blocks and is preserved",
        (not res["ok"])
        and (work / "a.txt").read_text() == "local edit\n"
        and res.get("overlapping") == ["a.txt"],
        f"blockers={res.get('blockers')} overlapping={res.get('overlapping')}",
    )

    # 6b. The stranding case: a strictly-behind remote whose incoming commit
    # touches a different path must fast-forward over a dirty index, and the
    # staged content must survive byte for byte, because that staged content
    # is the review baseline the remedy approver reads.
    d = base / "dirty-disjoint-ff"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "c.txt").write_text("staged baseline\n")
    git(work, "add", "c.txt")
    staged_blob = git(work, "rev-parse", ":c.txt")[1]
    index_before = git(work, "diff", "--cached", "--name-only")[1]
    _remote_commit(remote, d, "w")  # touches a.txt only
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: ff over a disjoint dirty index keeps the review baseline",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and git(work, "rev-parse", ":c.txt")[1] == staged_blob
        and git(work, "diff", "--cached", "--name-only")[1] == index_before
        and (work / "c.txt").read_text() == "staged baseline\n"
        and (work / "a.txt").read_text() == "two\n"
        and res.get("carried") == ["c.txt"],
        f"action={res.get('action')} carried={res.get('carried')}",
    )

    # 6c. The same overlap must still refuse, and must leave the index
    # byte-identical: a refusal that half-integrates is worse than no attempt.
    d = base / "overlap-preserves-index"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "a.txt").write_text("local edit\n")
    git(work, "add", "a.txt")
    index_before = git(work, "diff", "--cached")[1]
    head_before = git(work, "rev-parse", "HEAD")[1]
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: an overlapping path refuses and the index is untouched",
        (not res["ok"])
        and res.get("overlapping") == ["a.txt"]
        and git(work, "diff", "--cached")[1] == index_before
        and git(work, "rev-parse", "HEAD")[1] == head_before
        and (work / "a.txt").read_text() == "local edit\n"
        and not (work / ".git" / "MERGE_HEAD").exists(),
        f"overlapping={res.get('overlapping')}",
    )

    # 6d. An empty index must behave exactly as before: the overlap test has
    # nothing to find, so the fast-forward is the plain one.
    d = base / "clean-index"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: a clean index is unchanged by the overlap test",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and res.get("carried") == []
        and "overlapping" not in res,
        f"action={res.get('action')} carried={res.get('carried')}",
    )

    # 6e. A diverged history with an unstaged, disjoint edit: git carries it,
    # so the pipeline must let it through rather than refuse on a dirty tree.
    d = base / "diverged-unstaged-disjoint"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    (work / "b.txt").write_text("b1\nremediator fix\n")
    _remote_commit(remote, d, "w")  # touches a.txt only
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: a diverged merge carries an unstaged disjoint edit",
        res["ok"]
        and res["action"] == "merged"
        and (work / "a.txt").read_text() == "two\n"
        and (work / "b.txt").read_text() == "b1\nremediator fix\n",
        f"action={res.get('action')} blockers={res.get('blockers')}",
    )

    # 6f. A diverged history with an unstaged edit to a path the incoming
    # commits change must refuse before git runs.
    d = base / "diverged-unstaged-overlap"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    (work / "a.txt").write_text("a1\nlocal edit\n")
    index_before = git(work, "diff", "--cached")[1]
    head_before = git(work, "rev-parse", "HEAD")[1]
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: a diverged merge refuses an overlapping unstaged edit",
        (not res["ok"])
        and res.get("overlapping") == ["a.txt"]
        and git(work, "diff", "--cached")[1] == index_before
        and git(work, "rev-parse", "HEAD")[1] == head_before
        and (work / "a.txt").read_text() == "a1\nlocal edit\n",
        f"overlapping={res.get('overlapping')}",
    )

    # 7. a staged change blocks a diverged merge before any merge runs.
    d = base / "staged"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _local_commit(work, "w")
    (work / "c.txt").write_text("staged\n")
    git(work, "add", "c.txt")
    _remote_commit(remote, d, "w")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: staged change blocks a diverged merge before git runs",
        (not res["ok"])
        and res.get("staged") == ["c.txt"]
        and res.get("relation") == "diverged"
        and "index to match HEAD" in res["blockers"][-1],
        f"staged={res.get('staged')} blockers={res.get('blockers')}",
    )

    # 7a. The private checkout is normally dirty after a phase: the runner
    # writes ledger.json/run.json after finalize's commit. When local HEAD
    # already equals the remote, start sync is a no-op and must not touch
    # those artifacts.
    d = base / "dirty-equal"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "runs").mkdir()
    (work / "runs" / "ledger.json").write_text("{}\n")
    git(work, "add", "runs/ledger.json")
    git(work, "commit", "-q", "-m", "add ledger")
    git(work, "push", "-q", "origin", "main")
    (work / "runs" / "ledger.json").write_text('{"finalize": true}\n')
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: dirty run artifacts do not block an up-to-date start",
        res["ok"]
        and res["action"] == "up-to-date"
        and (work / "runs" / "ledger.json").read_text() == '{"finalize": true}\n',
        f"action={res.get('action')}",
    )

    # 7b. When the remote has moved, a fast-forward preserves dirty run
    # artifacts it does not touch.
    d = base / "dirty-unrelated"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    (work / "runs").mkdir()
    (work / "runs" / "run.json").write_text("{}\n")
    git(work, "add", "runs/run.json")
    git(work, "commit", "-q", "-m", "add run")
    git(work, "push", "-q", "origin", "main")
    (work / "runs" / "run.json").write_text('{"state": "completed"}\n')
    _remote_commit(remote, d, "w")  # touches a.txt only
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: ff preserves unrelated dirty run artifacts",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and (work / "runs" / "run.json").read_text() == '{"state": "completed"}\n'
        and (work / "a.txt").read_text() == "two\n",
        f"action={res.get('action')}",
    )

    # 8. sync_all fast-forwards both checkouts from their own remotes.
    d = base / "both"
    d.mkdir()
    pub_remote, pub = _init_remote(d, "pub")
    _remote_commit(pub_remote, d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    _remote_commit(priv_remote, d, "priv")
    res = sync_all(pub, "start")
    _check(
        checks,
        "sync: both checkouts fast-forward from own remotes",
        res["ok"] and all(r["action"] == "fast-forwarded" for r in res["repos"]),
        res["summary"],
    )

    # 9. a shared remote URL refuses both checkouts (a private tree and a
    # public one).
    d = base / "shared"
    d.mkdir()
    remote, pub = _init_remote(d, "w")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    res = sync_all(pub, "start")
    _check(
        checks,
        "sync: shared remote URL refuses both checkouts",
        (not res["ok"]) and "share a Git push/fetch destination" in res["summary"],
        res["summary"],
    )

    # 9b. a private pushurl aimed at the public remote is refused.
    d = base / "pushurl"
    d.mkdir()
    public_remote, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    private_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(private_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "remote", "set-url", "--push", "origin", str(public_remote))
    res = sync_all(pub, "start")
    _check(
        checks,
        "sync: public pushurl boundary refuses both checkouts",
        (not res["ok"]) and "share a Git push/fetch destination" in res["summary"],
        res["summary"],
    )

    # 10. a fresh start commits and pushes only the private repo's runs/
    # leftovers, then the private tree is clean.
    d = base / "runstate"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    (nested / "runs").mkdir()
    (nested / "runs" / "ledger.json").write_text("{}\n")
    git(nested, "add", "runs/ledger.json")
    git(nested, "commit", "-q", "-m", "ledger")
    git(nested, "push", "-q", "origin", "main")
    (nested / "runs" / "ledger.json").write_text('{"finalize": true}\n')
    res = commit_leftover_run_state(pub, "pipeline: test")
    _check(
        checks,
        "sync: start commits and pushes runs/ leftovers",
        res["ok"]
        and not dirty_paths(nested)
        and git(nested, "rev-parse", "HEAD")[1]
        == git(nested, "rev-parse", "origin/main")[1],
        res["summary"],
    )

    # 10b. a dirty file outside runs/ blocks the start cleanup so a
    # half-finished edit is never auto-published.
    d = base / "runstate-outside"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    (nested / "priv.txt").write_text("human edit\n")
    res = commit_leftover_run_state(pub, "pipeline: test")
    _check(
        checks,
        "sync: start refuses dirty files outside runs/",
        (not res["ok"]) and "outside runs/" in res["summary"],
        res["summary"],
    )

    # 11. Phase-scoped cleanup publishes only the requested run directory.
    d = base / "runstate-phase"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    for number in (100440, 100601):
        path = nested / "runs" / f"phase-{number:06d}"
        path.mkdir(parents=True)
        (path / "run.json").write_text('{"state": "running"}\n')
    git(nested, "add", "runs")
    git(nested, "commit", "-q", "-m", "run dirs")
    git(nested, "push", "-q", "origin", "main")
    (nested / "runs" / "phase-100440" / "run.json").write_text(
        '{"state": "completed"}\n'
    )
    (nested / "runs" / "phase-100601" / "run.json").write_text('{"state": "blocked"}\n')
    res = commit_leftover_run_state(pub, "pipeline: phase test", phase=100440)
    _check(
        checks,
        "sync: cleanup is scoped to the current phase",
        res["ok"]
        and json.loads((nested / "runs" / "phase-100440" / "run.json").read_text())[
            "state"
        ]
        == "completed"
        and json.loads((nested / "runs" / "phase-100601" / "run.json").read_text())[
            "state"
        ]
        == "blocked"
        and "runs/phase-100601/run.json" in dirty_paths(nested),
        res["summary"],
    )

    # 11b. A finished phase's closing run state is committed and pushed by
    # --mode clean, and only that phase's state. This is the defect that halted
    # phase 100901: the runner writes run.json, ledger.json, resume.json's
    # deletion and the usage file after finalize's publish commit, the next
    # phase's publish saw them dirty and refused. It must work while the public
    # checkout is mid-work, which is why the check dirties it.
    d = base / "runstate-closeout"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(nested, "config", "user.email", "sync-test@example.com")
    git(nested, "config", "user.name", "sync test")
    for number in (100440, 100601):
        path = nested / "runs" / f"phase-{number:06d}"
        path.mkdir(parents=True)
        (path / "run.json").write_text('{"state": "running"}\n')
        (path / "resume.json").write_text('{"current": "finalize"}\n')
    git(nested, "add", "runs")
    git(nested, "commit", "-q", "-m", "run dirs")
    git(nested, "push", "-q", "origin", "main")
    phase = nested / "runs" / "phase-100440"
    (phase / "run.json").write_text('{"state": "completed"}\n')
    (phase / "ledger.json").write_text('{"steps": {}}\n')
    (phase / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
    (phase / "finalize-usage-r1.jsonl").write_text('{"ok": true}\n')
    (phase / "resume.json").unlink()
    # Another phase's partial record, which must be left where it is.
    (nested / "runs" / "phase-100601" / "run.json").write_text('{"state": "blocked"}\n')
    (pub / "uncommitted-public.txt").write_text("mid-work\n")
    res = mode_clean(pub, 100440)
    dirty_after = dirty_paths(nested)
    _check(
        checks,
        "sync: clean commits a finished phase's closing run state",
        res["ok"]
        and not [p for p in dirty_after if p.startswith("runs/phase-100440/")]
        and "runs/phase-100601/run.json" in dirty_after
        and json.loads((phase / "run.json").read_text())["state"] == "completed",
        f"{res['summary']}; dirty={dirty_after}",
    )

    # 11c. With no --phase, --mode clean is the fresh-start sweep: every phase's
    # leftover run state is landed, each scoped to its own directory, so a phase
    # whose run ended without its own closing commit cannot block the next
    # phase's publish. The public checkout is mid-work and must stay untouched.
    res = mode_clean(pub)
    dirty_after_all = dirty_paths(nested)
    _check(
        checks,
        "sync: clean with no phase lands every phase's leftover run state",
        res["ok"]
        and not [
            p
            for p in dirty_after_all
            if p.startswith("runs/phase-100440/") or p.startswith("runs/phase-100601/")
        ]
        and "uncommitted-public.txt" in dirty_paths(pub),
        f"{res['summary']}; dirty={dirty_after_all}",
    )

    _check(
        checks,
        "publish: coordination failure maps to exit 2",
        result_exit_code({"ok": False, "state": "COORDINATION_UNAVAILABLE"}) == 2
        and result_exit_code({"ok": False, "state": "FENCED"}) == 1
        and result_exit_code({"ok": True, "state": "PUBLISHED"}) == 0
        and result_exit_code({"ok": True, "state": "AWAITING_REVIEW"}) == 0,
        "state-specific exit codes",
    )
    receipt_ok, receipt_detail = _publication_receipt_check(base)
    _check(checks, "publish: receipt-backed completion", receipt_ok, receipt_detail)

    group_ok, group_detail = _group_publication_check(base)
    _check(
        checks,
        "group: one branch, one pull request, one receipt per member",
        group_ok,
        group_detail,
    )

    race_ok, race_detail = _publish_while_writing_check(base)
    _check(
        checks,
        "publish: succeeds first try with the engine writing the marker",
        race_ok,
        race_detail,
    )
    for name, ok, detail in _publish_still_refuses_check(base):
        _check(checks, f"publish: {name}", ok, detail)
    for name, ok, detail in _phase_branch_name_checks(base):
        _check(checks, f"branch name: {name}", ok, detail)
    for name, ok, detail in _phase_branch_checks(base):
        _check(checks, f"branch: {name}", ok, detail)
    for name, ok, detail in _branch_pair_checks(base):
        _check(checks, f"branch pair: {name}", ok, detail)
    for name, ok, detail in _merge_gate_checks(base):
        _check(checks, f"merge gate: {name}", ok, detail)
    for name, ok, detail in _receipt_carry_checks(base):
        _check(checks, f"receipt carry: {name}", ok, detail)
    for name, ok, detail in _sequencing_checks(base):
        _check(checks, f"sequencing: {name}", ok, detail)
    for name, ok, detail in _tidy_and_dispatch_checks(base):
        _check(checks, f"dispatch: {name}", ok, detail)
    for name, ok, detail in _two_phase_stack_checks(base):
        _check(checks, f"stack: {name}", ok, detail)
    for name, ok, detail in _stale_receipt_checks(base):
        _check(checks, f"stale receipt: {name}", ok, detail)
    for name, ok, detail in _tree_proof_checks(base):
        _check(checks, f"tree proof: {name}", ok, detail)

    portable_ok, portable_detail = _portable_publication_check(base)
    _check(checks, "publish: custom project conventions", portable_ok, portable_detail)

    for name, ok, detail in _handoff_checks(base):
        _check(checks, f"handoff: {name}", ok, detail)
    for name, ok, detail in _group_branch_collision_checks(base):
        _check(checks, f"group name: {name}", ok, detail)

    return checks


def _handoff_checks(base):
    """A release handoff pushes the work in progress, and not onto the integration branch.

    Both halves matter. An owner who releases with nothing pushed hands over an
    empty branch, and one who pushes unfinished work onto the integration branch
    puts unreviewed code where everyone pulls.
    """
    d = base / "handoff"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "handoff")
    _configure_self_test(pub, pub / BOARD_REL)
    (pub / "unfinished.txt").write_text("half a phase\n", encoding="utf-8")
    on_integration = mode_checkpoint(pub)
    branch = "feature/group-cache-warm"
    made = ensure_phase_branch(pub, branch)
    if not made.get("ok"):
        return [("the handoff fixture could not cut a phase branch", False, str(made))]
    (pub / "unfinished.txt").write_text("half a phase\n", encoding="utf-8")
    pushed = mode_checkpoint(pub)
    head = git(pub, "rev-parse", "HEAD")[1]
    remote_head = git(pub, "rev-parse", f"origin/{branch}")[1]
    return [
        (
            "the integration branch is refused",
            not on_integration.get("ok")
            and "work in progress" in json.dumps(on_integration),
            f"state={on_integration.get('state')} {on_integration.get('summary')}",
        ),
        (
            "work in progress is committed and pushed on the phase branch",
            pushed.get("ok")
            and pushed.get("action") == "committed-and-pushed"
            and remote_head == head,
            f"ok={pushed.get('ok')} action={pushed.get('action')} "
            f"remote={remote_head[:8]} head={head[:8]}",
        ),
    ]


def _write_group_phases(roadmap, members, behaviour):
    """Two phase documents declaring one symmetric group, in a fixture roadmap."""
    declaration = f"`together: {members[0]:06d}, {members[1]:06d}`"
    for number in members:
        (roadmap / f"phase-{number:06d}-{behaviour}.md").write_text(
            f"# {number}. Group member\n\n- [ ] Work to do.\n\n"
            f"### Reservation Group\n\n{declaration}\n",
            encoding="utf-8",
        )


def _group_branch_collision_checks(base):
    """Two groups that derive one branch name are refused, and one group is not.

    A group branch carries no phase number, so the lowest member's behaviour word
    is all that separates one group's branch from another's.
    """
    results = []
    mine = ["100020", "100021"]
    d = base / "group-name"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "group-name")
    roadmap = pub / "roadmap"
    roadmap.mkdir()
    _write_group_phases(roadmap, (100020, 100021), "cache-warm")
    _write_group_phases(roadmap, (100040, 100041), "warm-cache")
    _configure_self_test(pub, pub / BOARD_REL, roadmap=roadmap)
    branch = group_branch(pub, mine)
    same = assert_group_branch_unique(pub, branch, mine)
    results.append(
        (
            "a group is not its own collision, and its branch comes from its lowest member",
            same.get("ok") and branch == "feature/group-cache-warm",
            f"ok={same.get('ok')} branch={branch} {same.get('summary')}",
        )
    )

    d = base / "group-name-clash"
    d.mkdir()
    _public_remote, pub = _init_remote(d, "group-name-clash")
    roadmap = pub / "roadmap"
    roadmap.mkdir()
    _write_group_phases(roadmap, (100020, 100021), "cache-warm")
    _write_group_phases(roadmap, (100040, 100041), "cache-warm")
    _configure_self_test(pub, pub / BOARD_REL, roadmap=roadmap)
    clash = assert_group_branch_unique(pub, branch, ["100040", "100041"])
    results.append(
        (
            "two groups deriving one branch are refused",
            not clash.get("ok") and "also the branch for group" in json.dumps(clash),
            f"{clash.get('summary')} {json.dumps(clash)}",
        )
    )
    return results


def self_test():
    checks = self_test_checks()
    ok = all(c["ok"] for c in checks)
    print(
        json.dumps(
            {"sync_self_test": "pass" if ok else "fail", "checks": checks}, indent=2
        )
    )
    return 0 if ok else 1


def result_exit_code(out):
    if out.get("state") in {"COORDINATION_UNAVAILABLE", "CONFIG_ERROR"}:
        return 2
    return 0 if out.get("ok") else 1


def mode_branch(root, phase, roadmap, roadmap_dir=None, group=None):
    """Put the public checkout on this phase's branch, then report.

    Split out of ``main`` so the self-test can drive the whole mode, refusals
    included. The dispatch used to live inline in ``main``, which put every
    refusal on this path outside the reach of any test — the one place a
    fail-closed branch must never be.

    ``roadmap_dir`` is the folder the phase file was chosen from, so the
    uniqueness check below scans the same phases the selector saw.

    ``group`` lists a reservation group's declared members, and gives the group
    the one branch every member publishes on. A group has no single file to
    derive a name from, so its branch is derived from its lowest member and
    checked for collisions like any other branch name.

    Returns the mode's result dict.
    """
    root = Path(root).resolve()
    # The branch no longer carries the phase number, so the behaviour is the
    # only thing separating two phases' branches. A duplicate is refused here,
    # before either checkout is touched.
    unique = assert_phase_branch_unique(
        root,
        phase,
        roadmap,
        roadmap_dir,
        branch=group_branch(root, group, roadmap_dir) if group else None,
    )
    if not unique["ok"]:
        return {
            "ok": False,
            "mode": "branch",
            "branch": unique["branch"],
            "repos": [unique],
            "summary": summarize([unique], "branch"),
        }
    branch = unique["branch"]
    if group:
        # A group branch comes from the group's lowest member, so two groups can
        # derive one name even when no phase branch collides with it.
        shared = assert_group_branch_unique(root, branch, group, roadmap_dir)
        if not shared["ok"]:
            return {
                "ok": False,
                "mode": "branch",
                "branch": branch,
                "repos": [unique, shared],
                "summary": summarize([shared], "branch"),
            }
    # The base is the open predecessor's branch when there is one. Without this
    # the phase starts from the integration branch, which does not contain the
    # previous phase's code, and the phase fails on a symbol that is not there.
    # A predecessor named here but absent from the checkout stops the launch
    # rather than falling back to the integration branch, because the fallback
    # looks exactly like a correct launch and produces a phase built on missing
    # code.
    predecessor = open_predecessor(root, phase)
    if predecessor and predecessor.get("branch") == branch:
        # Another member of this phase's own reservation group. One branch covers
        # the whole group, so that member's work is on the branch this launch is
        # about to cut or resume, and there is no separate base to prove. Without
        # this the missing-branch refusal below fires on the first tick of every
        # group, because the group branch does not exist until that tick creates
        # it, and the members after the first would never launch.
        predecessor = None
    base = None
    if predecessor:
        rc, _, _ = git(
            root, "rev-parse", "--verify", f"refs/heads/{predecessor['branch']}"
        )
        if rc != 0:
            rc, _, _ = git(
                root,
                "rev-parse",
                "--verify",
                f"refs/remotes/origin/{predecessor['branch']}",
            )
        if rc != 0:
            # The branch is gone and this phase is still treated as open, so
            # there is no tree to build on and no proof the work landed. Naming
            # the phase, the pull request and the missing proof is the point:
            # this is the refusal the line retried silently for two days, and
            # the line an operator reads has to say what to do about it.
            landed = phase_work_on_integration(root, predecessor["phase"])
            reason = (
                f"predecessor phase {predecessor['phase']:06d} names base "
                f"{predecessor['branch']!r}, which does not exist in this "
                f"checkout, and nothing confirms its pull request "
                f"{predecessor.get('pull_request') or '(none recorded)'} "
                f"was merged"
            )
            if landed.get("github", {}).get("state") == "unknown":
                reason += (
                    f" (GitHub could not be asked: "
                    f"{landed['github'].get('detail')}). A squash merge "
                    f"of several commits leaves no local trace, so the "
                    f"commit, content and patch tests cannot confirm it; "
                    f"restore gh and rerun"
                )
            else:
                reason += (
                    " (its branch was deleted and no local proof of the "
                    "merge remains: restore the branch, or let the merge "
                    "check record the merge, then rerun)"
                )
            refusal = blocked(
                {
                    "repo": str(root),
                    "branch": branch,
                    "base": predecessor["branch"],
                    "predecessor_phase": f"{int(predecessor['phase']):06d}",
                    "pull_request": predecessor.get("pull_request"),
                    "landed": landed,
                },
                reason,
                hint="the phase branch is missing or was deleted; restore it, "
                "or let the merge check confirm the pull request, then rerun",
            )
            return {
                "ok": False,
                "mode": "branch",
                "branch": branch,
                "base": predecessor["branch"],
                "predecessor": predecessor,
                "repos": [refusal],
                "summary": summarize([refusal], "branch"),
            }
        base = predecessor["branch"]
    made = branch_phase_checkouts(root, branch, base=base)
    ok = all(m["ok"] for m in made)
    out = {
        "ok": ok,
        "mode": "branch",
        "branch": branch,
        "base": base or integration_branch(root),
        "predecessor": predecessor,
        "repos": made,
        "summary": summarize(made, "branch"),
    }
    if ok and predecessor and out["base"] == predecessor["branch"]:
        # Did the predecessor move since this phase was built on it? The
        # existence check above cannot see that: a force-pushed branch still
        # exists, so the phase would silently end up based on commits nobody
        # reviewed. Reported as a warning rather than a stop, because a
        # predecessor that legitimately gained commits is ordinary once more
        # than one phase is open at once, and refusing to launch would put the
        # line behind a human for something it can describe precisely.
        rc_h, head_now, _ = git(
            root, "rev-parse", "--verify", f"refs/heads/{predecessor['branch']}"
        )
        if rc_h != 0:
            rc_h, head_now, _ = git(
                root,
                "rev-parse",
                "--verify",
                f"refs/remotes/origin/{predecessor['branch']}",
            )
        recorded = predecessor.get("head")
        if rc_h == 0 and recorded and head_now != recorded:
            out["predecessor_moved"] = {
                "branch": predecessor["branch"],
                "phase": f"{int(predecessor['phase']):06d}",
                "recorded_head": recorded,
                "current_head": head_now,
            }
            out["warning"] = (
                f"the predecessor branch {predecessor['branch']!r} has moved "
                f"since this phase was recorded as based on it "
                f"({recorded[:12]} -> {head_now[:12]}); the phase branch "
                "still contains the commits it was built from, but a "
                "force-pushed predecessor means this pull request will show "
                "changes that no longer match what the predecessor "
                "published"
            )
    return out


def mode_published(root, phase):
    """Confirm a human merged the phase, mark it published, and tidy up.

    Order matters. The receipt is only written after the merge is verified, and
    the checkout only moves after the receipt says the phase is done. Doing
    either first would let a phase look finished while its code is still
    unreviewed.

    The move is best-effort and reported separately from the merge, because the
    two can succeed and fail independently. When they do, ``tidy_published_
    checkout`` completes the move on a later tick; it used to be that this
    path's own retry was the only one, and removing the line's review wait
    removed it.
    """
    root = Path(root).resolve()
    marked = mark_phase_published(root, phase)
    if not marked["ok"]:
        return {
            "ok": False,
            "mode": "published",
            "state": "BLOCKED",
            "repos": [marked],
            "summary": summarize([marked], "published"),
        }
    repos = [marked]
    # A member of a group does not take the checkout with it. One branch and one
    # pull request cover the whole group, so the members that have not run yet
    # still need that branch; moving the checkout to the integration branch here
    # would take it out from under the next member's launch.
    receipt = phase_receipt(root, phase)
    members = list(receipt.get("group") or []) if isinstance(receipt, dict) else []
    if members and not _group_all_published(root, members):
        pending = [m for m in members if not _member_published(root, m)]
        repos.append(
            {
                "repo": str(root),
                "ok": True,
                "action": "group-still-open",
                "group": members,
                "pending": pending,
                "hint": f"{len(pending)} member(s) of this reservation "
                "group are not published; the checkout stays on "
                "the group branch",
            }
        )
        return {
            "ok": True,
            "mode": "published",
            "state": "PUBLISHED-GROUP-PENDING",
            "repos": repos,
            "summary": summarize(repos, "published"),
        }
    repos.append(return_to_integration(root))
    # The private checkout stays on the integration branch by design, so only
    # the public one can drift. A stop there is still a stop: report it rather
    # than claiming the phase is tidied when the checkout is not.
    ok = all(r["ok"] for r in repos)
    state = "PUBLISHED" if ok else "PUBLISHED-TIDY-PENDING"
    return {
        "ok": ok,
        "mode": "published",
        "state": state,
        "repos": repos,
        "summary": summarize(repos, "published"),
    }


def mode_resync(root):
    """Merge the integration branch into both checkouts' current branches."""
    root = Path(root).resolve()
    # The public checkout is the one that can be on a phase branch. The private
    # one is always on the integration branch and reports itself as a no-op,
    # which is named rather than dropped so the two stay distinguishable in the
    # run record.
    targets = [root] + ([board_repo(root)] if is_git_repo(board_repo(root)) else [])
    repos = [resync_integration(r) for r in targets]
    ok = all(r["ok"] for r in repos)
    return {
        "ok": ok,
        "mode": "resync",
        "repos": repos,
        "summary": summarize(repos, "resync"),
    }


def mode_tidy(root):
    """Return a checkout sitting on a published phase branch to master."""
    root = Path(root).resolve()
    repos = [tidy_published_checkout(root)]
    nested = board_repo(root)
    if is_git_repo(nested):
        repos.append(tidy_published_checkout(nested))
    ok = all(r["ok"] for r in repos)
    return {
        "ok": ok,
        "mode": "tidy",
        "repos": repos,
        "summary": summarize(repos, "tidy"),
    }


def _run_phase_numbers(root):
    """Every phase number with a run directory under ``runs/``, lowest first."""
    numbers = []
    for path in sorted(runs_dir(root).glob("phase-*")):
        match = re.fullmatch(r"phase-(\d+)", path.name)
        if match and path.is_dir():
            numbers.append(int(match.group(1)))
    return numbers


def mode_clean(root, phase=None):
    """Commit and push one phase's leftover private run state, and only that.

    The private-only sibling of ``commit_leftover_run_state``. It runs after a
    phase's own run has finished, when the runner has written the run state that
    lands after finalize's publish commit: the ledger's finalize row, the run
    marker, the usage files, the deleted resume record, and the finalize log.
    Those writes were never committed, so the next phase's publish found them
    dirty, refused with ``working tree is dirty after phase cleanup`` and halted
    the line.

    It does not refuse because the public checkout is dirty, and that is the
    whole difference. A phase's closing run-state writes belong to the private
    repository, and the public checkout is legitimately mid-work while the phase
    it just finished sits on its own branch. The pathspec keeps the phase scope
    the publish helper already relies on: this never stages another phase's run
    state.

    With ``phase`` omitted, every phase's leftover run state is committed, each
    scoped to its own directory. This is the fresh-start sweep: a phase whose run
    ended without its own closing commit — an interruption, a failed clean, or a
    run from before the cleanup existed — leaves writes the next phase's publish
    refuses on, and this lands them before that launch. Each directory stays its
    own pathspec, so a later phase's partial records are still never swept into
    an earlier phase's commit.
    """
    root = Path(root).resolve()
    private = board_repo(root)
    if not is_git_repo(private):
        return {
            "ok": True,
            "mode": "clean",
            "repos": [],
            "pathspec": "runs",
            "summary": f"clean sync ok: no private checkout at {private}",
        }
    if phase is None:
        # Only phases with leftover dirt are committed. A phase already clean
        # would make `git commit` report "no changes added to commit" while a
        # sibling phase is still dirty, which the helper reads as a failure.
        dirty = set(dirty_paths(private))
        numbers = []
        for number in _run_phase_numbers(root):
            try:
                pathspec = _phase_run_pathspec(number, root)
            except ConfigError:
                # A run directory whose phase file is gone cannot be scoped to a
                # pathspec, so this sweep cannot land it. Skipping it keeps one
                # stale directory from stopping the sweep for every other phase,
                # which is the only reason the sweep exists: the next phase's
                # publish otherwise refuses on run state nobody landed.
                continue
            prefix = pathspec + "/"
            if any(p == pathspec or p.startswith(prefix) for p in dirty):
                numbers.append(number)
        if not numbers:
            return {
                "ok": True,
                "mode": "clean",
                "repos": [],
                "pathspec": "runs",
                "summary": "clean sync ok: no leftover run state",
            }
        results = [
            _commit_and_push_run_state(
                private,
                f"runs: close out phase {number:06d} run state",
                pathspec=_phase_run_pathspec(number, root),
            )
            for number in numbers
        ]
        ok = all(res.get("ok") for res in results)
        return {
            "ok": ok,
            "mode": "clean",
            "repos": results,
            "pathspec": "runs",
            "summary": summarize(results, "clean"),
        }
    pathspec = _phase_run_pathspec(phase, root)
    res = _commit_and_push_run_state(
        private, f"runs: close out phase {int(phase):06d} run state", pathspec=pathspec
    )
    return {
        "ok": bool(res.get("ok")),
        "mode": "clean",
        "repos": [res],
        "pathspec": pathspec,
        "summary": summarize([res], "clean"),
    }


def mode_checkpoint(root):
    """Commit and push a checkout's work in progress on the branch it is on.

    This is the handoff half of a release. The owner is giving the reservation
    back, so the work it did has to reach the branch before the next owner picks
    the group up; otherwise the handoff is a restart. The integration branch is
    refused, because pushing unfinished product code onto the branch everyone
    pulls is the thing the phase branch exists to prevent. A checkout with
    nothing to commit is a no-op, not a failure, so an owner with nothing to push
    still releases.

    Returns a result dict; ok is the success flag.
    """
    root = Path(root).resolve()
    info = {"repo": str(root)}
    if not is_git_repo(root):
        return dict(
            info, ok=True, action="checkpoint-skipped", hint="not a git working tree"
        )
    branch, remote, _remote_branch, why = resolve_upstream(root)
    if why:
        return blocked(info, why)
    if branch == integration_branch(root):
        return blocked(
            info,
            f"refusing to push work in progress on {branch!r}",
            hint="a handoff is the phase branch, not the integration branch",
        )
    rc, out, err = git(root, "add", "-A")
    if rc != 0:
        return blocked(info, f"could not stage the checkout: {err or out}")
    rc, out, err = git(
        root, "commit", "-q", "-m", f"checkpoint: work in progress on {branch}"
    )
    action = "committed-and-pushed"
    if rc != 0:
        combined = err or out
        if "nothing to commit" in combined.lower():
            action = "pushed"
        else:
            return blocked(info, f"checkpoint commit failed: {combined}")
    rc, out, err = git(root, "push", remote, branch)
    if rc != 0:
        return blocked(info, f"checkpoint push failed: {err or out}")
    head = git(root, "rev-parse", "HEAD")[1]
    return dict(info, ok=True, action=action, branch=branch, head=head)


def main():
    ap = argparse.ArgumentParser(description="Fail-closed git sync.")
    ap.add_argument(
        "--root",
        "--repo-root",
        dest="config_repo_root",
        help="repo root; syncs it and the configured private checkout",
    )
    ap.add_argument("--config")
    ap.add_argument("--driver", dest="config_driver")
    ap.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    ap.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    ap.add_argument("--repo", default=None, help="sync one checkout instead of --root")
    ap.add_argument(
        "--mode",
        choices=[
            "start",
            "push",
            "publish",
            "branch",
            "published",
            "resync",
            "tidy",
            "clean",
            "checkpoint",
        ],
        default="start",
        help="start: sync before a phase; push: verify a "
        "fast-forward; publish: claim-check and push a phase's "
        "branch; branch: put the public checkout on the phase "
        "branch; published: confirm a human merged the pull "
        "request, mark the phase published, and return the "
        "public checkout to the integration branch; resync: "
        "merge the integration branch into the phase branch, "
        "leaving a conflict for the agent to finish; tidy: "
        "return a checkout to the integration branch when the "
        "phase it is on is already recorded as published; "
        "clean: commit and push leftover private run state, "
        "for one --phase or for every phase when it is omitted; "
        "checkpoint: commit and push a checkout's work in "
        "progress on the branch it is on, for a release handoff",
    )
    ap.add_argument(
        "--phase", default=None, help="phase number for --mode publish or --mode clean"
    )
    ap.add_argument(
        "--reservation-file",
        default=None,
        help="phase-local reservation.json for --mode publish",
    )
    ap.add_argument("--machine-id", default=None)
    ap.add_argument("--reservation-id", default=None)
    ap.add_argument("--generation", type=int, default=None)
    ap.add_argument(
        "--self-test", action="store_true", help="run stubbed-remote checks and exit"
    )
    ap.add_argument(
        "--roadmap", default=None, help="phase roadmap file name, for --mode branch"
    )
    ap.add_argument(
        "--group",
        action="append",
        default=None,
        help="a member of the reservation group this branch covers, "
        "for --mode branch; repeat once per member",
    )
    ap.add_argument(
        "--branch",
        default=None,
        help="the branch the public checkout must be on, for the start and push modes",
    )
    ap.add_argument(
        "--pull-request",
        default=None,
        help="pull request URL finalize opened, recorded in the "
        "receipt for --mode publish",
    )
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        conventions.configure_from_args(args)
        args.root = str(conventions.setting("repo-root"))
        if args.mode == "branch":
            if args.phase is None or not args.roadmap:
                raise ConfigError(
                    "--phase and --roadmap are required with --mode branch"
                )
            out = mode_branch(
                args.root,
                args.phase,
                args.roadmap,
                conventions.setting("roadmap-dir"),
                group=args.group,
            )
        elif args.mode == "publish":
            if args.phase is None:
                raise ConfigError("--phase is required with --mode publish")
            out = publish_phase(
                Path(args.root).resolve(),
                args.phase,
                reservation_file=args.reservation_file,
                machine_id=args.machine_id,
                reservation_id=args.reservation_id,
                generation=args.generation,
                pull_request=args.pull_request,
            )
        elif args.mode == "published":
            if args.phase is None:
                raise ConfigError("--phase is required with --mode published")
            out = mode_published(args.root, args.phase)
        elif args.mode == "clean":
            out = mode_clean(args.root, args.phase)
        elif args.mode == "resync":
            out = mode_resync(args.root)
        elif args.mode == "tidy":
            out = mode_tidy(args.root)
        elif args.mode == "checkpoint":
            out = mode_checkpoint(args.root)
        elif args.repo:
            res = sync_one(Path(args.repo).resolve(), args.mode, branch=args.branch)
            out = {
                "ok": res["ok"],
                "mode": args.mode,
                "repos": [res],
                "summary": summarize([res], args.mode),
            }
        else:
            out = sync_all(Path(args.root).resolve(), args.mode, branch=args.branch)
    except (ArgusConfigError, ConfigError, ValueError) as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": args.mode,
                    "state": "config_error",
                    "error": str(e),
                },
                indent=2,
            )
        )
        return 2
    print(json.dumps(out, indent=2))
    return result_exit_code(out)


if __name__ == "__main__":
    sys.exit(main())
