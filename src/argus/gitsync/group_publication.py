# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The reservation fence, and the receipt a publication writes for one phase or a group."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from argus.conventions import board_repo, phase_run_dir
from argus.gitsync.git import (
    ConfigError,
    blocked,
    current_branch,
    git,
    integration_branch,
    resolve_upstream,
)
from argus.gitsync.group_review import _group_pull_request
from argus.gitsync.paths import roadmap_folder
from argus.gitsync.receipts import (
    _published_head,
    phase_receipt,
    pull_request_ref,
)
from argus.gitsync.review import open_predecessor
from argus.gitsync.runstate import commit_leftover_run_state


def _declared_group(root: str | Path, phase: int) -> dict[str, Any] | None:
    """The reservation group a phase declares, or ``None``.

    Read from the roadmap documents, which is where a declaration lives. A
    malformed one is refused rather than read as "runs alone": a phase whose
    group nobody can read is not a phase that ran by itself, and publishing it
    as one would record the wrong branch. An absent declaration, and a machine
    with no roadmap folder to read, are the ordinary single-phase case.
    """
    from argus.roadmap import source as roadmap_source

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


def _member_published(root: str | Path, member: object) -> bool:
    """Whether one group member's receipt says it is published."""
    try:
        number = int(str(member).strip())
    except (TypeError, ValueError):
        return False
    receipt = phase_receipt(root, number)
    return receipt is not None and receipt.get("state") == "published"


def _group_all_published(root: str | Path, members: Iterable[Any] | None) -> bool:
    """Whether every member of a group is published.

    A group shares one branch and one pull request, so a member whose merge is
    recorded is not permission to leave that branch: the members that have not
    run yet still need it. A member with no receipt at all is not published,
    which is the answer that keeps the checkout where the group needs it.
    """
    members = list(members or ())
    return bool(members) and all(_member_published(root, member) for member in members)


def _write_publication_receipt(
    root: str | Path,
    phase: int,
    reservation: dict[str, Any],
    results: list[dict[str, Any]],
    engine_written: Iterable[str] = (),
    pull_request: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    private = cast("Path", board_repo(root))
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
    from argus.board.reservations import _now

    receipt: dict[str, Any] = {
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
    carried: dict[str, Any] | None = open_predecessor(root, phase)
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
    root: str | Path,
    phase: int,
    reservation_file: str | Path | None = None,
    machine_id: str | None = None,
    reservation_id: str | None = None,
    generation: int | str | None = None,
) -> dict[str, Any]:
    if reservation_file is None:
        reservation_file = phase_run_dir(root, phase) / "reservation.json"
    try:
        data = cast("dict[str, Any]", json.loads(Path(reservation_file).read_text()))
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


def _push_one(repo: str | Path) -> dict[str, Any]:
    branch, remote, _remote_branch, why = resolve_upstream(repo)
    if why:
        return blocked({"repo": str(repo)}, why)
    branch = cast("str", branch)
    remote = cast("str", remote)
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


__all__ = [
    "_group_all_published",
    "_load_phase_reservation",
    "_push_one",
    "_write_publication_receipt",
]
