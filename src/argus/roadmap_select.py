# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Claiming a phase, or a declared group, on the shared reservation board."""

from argus.phase_reservations import (
    CoordinationError,
    ReservationConflict,
    ReservationCooldown,
    reservation_file,
    save_reservation_file,
)
from argus.roadmap_evidence import reported_path


def _gitsync(_repo):
    """Return the publisher shipped with this Argus package."""
    try:
        from argus.gitsync import awaiting_review
    except ImportError as exc:
        raise ImportError("the Argus package is missing its bundled publisher") from exc
    return awaiting_review


def _awaiting_review(repo, number):
    """A phase whose pull request is waiting for a human, or None.

    A phase that has been pushed but not merged is finished work, not queue
    work. The selector must not offer it for another run, and it must not skip
    past it either: the line waits until a person merges.
    """
    return _gitsync(repo)(repo, number)


def _reservation_result(number, path, record, state="RESERVED"):
    return {
        "state": state,
        "phase": f"{number:06d}",
        "path": str(path),
        "machine_id": record.get("machine_id"),
        "status": record.get("status"),
        "reservation_id": record.get("reservation_id"),
        "generation": record.get("generation"),
        "run_id": record.get("run_id"),
    }


def _select_group(repo, number, path, group, machine_id, store, *, reserve, reviews):
    """Claim a declared group's unfinished members, or report why not.

    The remainder drops completed members. A member whose pull request is open
    is finished work too, so it is dropped from the claim and recorded under
    ``reviews``, which is what lets the driver run the merge check on it. If
    nothing is left to claim the group has no queue work this tick and ``None``
    is returned, so the caller moves to the next phase.
    """
    key = f"{number:06d}"
    remainder = list(group["remainder"])
    claimable = []
    for member in remainder:
        review = _awaiting_review(repo, int(member))
        if review:
            reviews.append(
                {
                    "phase": member,
                    "branch": review.get("branch"),
                    "pull_request": review.get("pull_request"),
                    "head": review.get("head"),
                }
            )
        else:
            claimable.append(member)
    if not claimable:
        return None
    if not reserve:
        result = _reservation_result(number, path, {}, "AVAILABLE")
        result["path"] = reported_path(repo, path)
        result["group"] = list(group["group"])
        result["remainder"] = remainder
        return result
    try:
        records = store.reserve_group(claimable, machine_id)
    except ReservationCooldown as exc:
        return {
            "state": "COOLDOWN",
            "phase": exc.phase,
            "path": reported_path(repo, path),
            "remaining_seconds": exc.remaining_seconds,
            "error": str(exc),
        }
    except ReservationConflict as exc:
        # The push race lost. Read the winner rather than force the claim. The
        # declared group travels with the refusal, because the tick that
        # continues this group — or takes it over from a dead owner — has to
        # know which array and which one branch it is running.
        latest, _latest_commit = store.read()
        winner = latest.get("phases", {}).get(exc.phase, exc.record) or {}
        return {
            "state": "WAIT_FOR_CLAIM",
            "phase": exc.phase,
            "path": reported_path(repo, path),
            "owner": winner.get("machine_id"),
            "status": winner.get("status"),
            "reservation_id": winner.get("reservation_id"),
            "generation": winner.get("generation"),
            "group": list(group["group"]),
        }
    for record in records:
        token_path = reservation_file(repo, record["phase"])
        try:
            save_reservation_file(token_path, record)
        except OSError as exc:
            raise CoordinationError(
                f"could not persist phase reservation token: {exc}"
            ) from exc
    lead = next((r for r in records if r["phase"] == key), records[0])
    result = _reservation_result(number, path, lead)
    result["path"] = reported_path(repo, path)
    result["group"] = list(group["group"])
    result["members"] = records
    return result
