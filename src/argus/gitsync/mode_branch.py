# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Putting the public checkout on the branch a phase or a group publishes from."""

from pathlib import Path
from typing import Any

from argus.gitsync.branch import branch_phase_checkouts
from argus.gitsync.git import git, integration_branch, summarize
from argus.gitsync.paths import (
    assert_group_branch_unique,
    assert_phase_branch_unique,
    group_branch,
)
from argus.gitsync.review import open_predecessor


def mode_branch(
    root: str | Path,
    phase: int | str,
    phases: str | None,
    phases_dir: str | Path | None = None,
    group: list[str] | None = None,
) -> dict[str, Any]:
    """Put the public checkout on this phase's branch, then report.

    Split out of ``main`` so the self-test can drive the whole mode, refusals
    included. The dispatch used to live inline in ``main``, which put every
    refusal on this path outside the reach of any test — the one place a
    fail-closed branch must never be.

    ``phases_dir`` is the folder the phase file was chosen from, so the
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
        phases,
        phases_dir,
        branch=group_branch(root, group, phases_dir) if group else None,
    )
    # A duplicate branch name is reported, not refused. The phase continues on
    # the branch it derives; the warning names the collision for the operator.
    warnings: list[str] = []
    if unique.get("warning"):
        warnings.append(str(unique["warning"]))
    branch = unique["branch"]
    if group:
        # A group branch comes from the group's lowest member, so two groups can
        # derive one name even when no phase branch collides with it.
        shared = assert_group_branch_unique(root, branch, group, phases_dir)
        if shared.get("warning"):
            warnings.append(str(shared["warning"]))
    # The base is the open predecessor's branch when there is one. Without this
    # the phase starts from the integration branch, which does not contain the
    # previous phase's code, and the phase fails on a symbol that is not there.
    # A predecessor named here but absent from the checkout stops the launch
    # rather than falling back to the integration branch, because the fallback
    # looks exactly like a correct launch and produces a phase built on missing
    # code.
    #
    # A member of this phase's own reservation group is no predecessor: one branch
    # covers the whole group, so that member's work is on the branch this launch is
    # about to cut or resume, and there is no separate base to prove. Without this
    # the missing-branch refusal below fires on the first tick of every group,
    # because the group branch does not exist until that tick creates it, and the
    # members after the first would never launch.
    predecessor = open_predecessor(root, phase, own_branch=branch)
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
            # The predecessor branch is gone. The phase's own branch is the
            # safe base; the warning names the missing branch and pull request.
            # Nothing here halts the line.
            warnings.append(
                f"predecessor phase {predecessor['phase']:06d} names base "
                f"{predecessor['branch']!r}, which does not exist; using the "
                f"phase's own branch (pull request "
                f"{predecessor.get('pull_request') or '(none recorded)'})"
            )
        else:
            base = predecessor["branch"]
    made = branch_phase_checkouts(root, branch, base=base)
    ok = all(m["ok"] for m in made)
    out: dict[str, Any] = {
        "ok": ok,
        "mode": "branch",
        "branch": branch,
        "base": base or integration_branch(root),
        "predecessor": predecessor,
        "repos": made,
        "summary": summarize(made, "branch"),
    }
    if warnings:
        out["warnings"] = warnings
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
