# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The publication helper: fence, lock, leftover commit, sync and push."""

from pathlib import Path
from typing import Any, cast

from argus.conventions import board_repo
from argus.gitsync.git import (
    ConfigError,
    _is_private_path,
    current_branch,
    dirty_paths,
    is_git_repo,
    summarize,
)
from argus.gitsync.group_gate import release_group_pull_request
from argus.gitsync.group_publication import (
    _load_phase_reservation,
    _push_one,
    _write_publication_receipt,
)
from argus.gitsync.group_review import _group_pending, update_group_pull_request
from argus.gitsync.review import open_predecessor
from argus.gitsync.runstate import commit_leftover_run_state, publish_dirt
from argus.gitsync.sync import mode_resync, sync_all


def publish_phase(
    root: str | Path,
    phase: int | str,
    *,
    reservation_file: str | Path | None = None,
    machine_id: str | None = None,
    reservation_id: str | None = None,
    generation: int | None = None,
    commit_run_state: bool = True,
    pull_request: str | None = None,
) -> dict[str, Any]:
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
        from argus.board.reservations import (
            PublicationBusy,
            ReservationFenced,
            ReservationStore,
        )
    except Exception as exc:  # noqa: BLE001 - normalize boundary errors
        return {
            "ok": False,
            "mode": "publish",
            "state": "COORDINATION_UNAVAILABLE",
            "error": str(exc),
            "phase": f"{int(phase):06d}",
        }
    try:
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

    results: list[Any] = []
    outcome: dict[str, Any] | None = None
    engine_churn: list[str] = []
    try:
        private = cast("Path", board_repo(root))
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
            if is_git_repo(private):
                dirty += [str(private / p) for p in dirty_paths(private)]
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
            for checkout in (root, private):
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
            push_results = [
                cast("dict[str, Any]", r)
                for r in results
                if isinstance(r, dict) and "repo" in r
            ]
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
            # The group's own answer, for the caller: the shared pull request
            # that covers these members, which members have not published yet,
            # and whether this publication was the group's last. The gate sits
            # beside it, in the one place that knows the group is finished: it
            # merges the fix pull requests waiting on the group branch and takes
            # the shared pull request out of draft once, when its checks are
            # green. Checks that are red or still running hold it, and the
            # driver's next ticks retry, so a publish never has to wait for CI.
            group: list[Any] = receipt.get("group") or []
            members = [str(member) for member in group]
            if members:
                pending = _group_pending(root, members, phase)
                outcome["group"] = members
                outcome["group_pending"] = pending
                outcome["group_last_unfinished"] = not pending
                updated = update_group_pull_request(
                    root, phase, members, receipt.get("pull_request")
                )
                if updated is not None:
                    outcome["pull_request_update"] = updated
                    if not updated.get("ok"):
                        outcome["pull_request_update_warning"] = (
                            f"the shared pull request was not fully updated: "
                            f"{updated.get('comment_detail')} / "
                            f"{updated.get('body_detail')}. The pull request "
                            f"may show an open checkbox or a missing comment; "
                            f"read the pull request body before merging."
                        )
                try:
                    gate = release_group_pull_request(
                        root,
                        phase,
                        members,
                        receipt.get("pull_request"),
                        receipt.get("branch"),
                    )
                except Exception as exc:  # noqa: BLE001 - the gate never undoes a publish
                    gate = {"state": "HELD", "reasons": [f"group gate failed: {exc}"]}
                if gate is not None:
                    outcome["group_gate"] = gate
            outcome["repos"] = results
            outcome["summary"] = summarize(
                [
                    cast("dict[str, Any]", r)
                    for r in results
                    if isinstance(r, dict) and "repo" in r
                ],
                "publish",
            )
            # A warning, never a block. This phase was built on an earlier
            # phase's branch, so its pull request carries that phase's work.
            # Merging this one alone lands code that was never reviewed as its
            # own phase, and only a human can decide whether that phase's work
            # should exist at all. Blocking here would put the human back on the
            # critical path, which is the thing this pipeline exists to remove.
            carried = open_predecessor(root, phase, own_branch=receipt.get("branch"))
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
