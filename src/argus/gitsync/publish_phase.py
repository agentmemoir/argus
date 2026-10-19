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
from argus.gitsync.group_publication import (
    _load_phase_reservation,
    _push_one,
    _write_publication_receipt,
)
from argus.gitsync.group_review import _group_pending, update_group_pull_request
from argus.gitsync.review import open_predecessor
from argus.gitsync.runstate import (
    commit_leftover_run_state,
    publish_dirt,
    runstate_repo,
)
from argus.gitsync.sync import mode_resync, separation_reason, sync_all


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
    directory, syncs both checkouts, and pushes. A failed sync is reported, not
    gated: the dirty-tree check is gone, and the push is what publishes. It never
    stages an unrelated phase, never uses a force push, and never merges: a
    phase's commits reach the integration branch only when a human merges the
    pull request this run's receipt points at.
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
    # The separation check runs before any commit or push: publication must
    # never send private run state to a public remote. A shared destination is
    # a retry with no push and no receipt.
    unsafe = separation_reason(root)
    if unsafe is not None:
        return {
            "ok": False,
            "mode": "publish",
            "phase": f"{phase:06d}",
            "state": "RETRY",
            "error": unsafe,
            "hint": "fix the Git destinations, then the next tick retries; nothing "
            "was pushed and no receipt was written",
        }
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
        run_repo = runstate_repo(root, phase)
        if commit_run_state:
            clean = commit_leftover_run_state(
                root,
                f"pipeline: publish run state for phase {int(phase):06d}",
                phase=phase,
            )
            results.append(clean)
            if not clean["ok"]:
                # A leftover commit that could not land is a repair: write no
                # receipt, leave the phase waiting, and retry next tick.
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "RETRY",
                    "repos": results,
                    "summary": clean["summary"],
                    "hint": "run state did not commit; the next tick retries and "
                    "no receipt is written",
                }
        if outcome is None:
            dirty = [p for p in dirty_paths(root) if not _is_private_path(root, p)]
            # The run state may live in its own checkout, which is neither the
            # public nor the board repository; its dirt is checked like the
            # board checkout's.
            for repo in dict.fromkeys((private, run_repo)):
                if repo is not None and repo != root and is_git_repo(repo):
                    dirty += [str(repo / p) for p in dirty_paths(repo)]
            # The engine that is publishing is the engine that rewrites the
            # heartbeat marker. The tree is no longer a gate: the run state is
            # committed above and the branch is pushed below. The engine-written
            # paths are still reported, so a reader can see which ones churned.
            _, engine = publish_dirt(dirty, root=root, phase=phase)
            engine_churn = engine
        if outcome is None:
            resync = mode_resync(root)
            results.append(resync)
        if outcome is None:
            sync = sync_all(root, "push")
            results.append(sync)
            if not sync.get("ok"):
                # The separation check refused the destinations (or another
                # sync step failed). Push nothing and write no receipt; the
                # phase stays waiting.
                outcome = {
                    "ok": False,
                    "mode": "publish",
                    "phase": f"{int(phase):06d}",
                    "state": "RETRY",
                    "repos": results,
                    "summary": sync.get("summary", ""),
                    "hint": "the checkouts could not be synced safely; no push "
                    "and no receipt, the next tick retries",
                }
        if outcome is None:
            checkouts = [root, private]
            if run_repo is not None and run_repo not in checkouts:
                checkouts.append(run_repo)
            for checkout in checkouts:
                if not is_git_repo(checkout):
                    continue
                store.assert_owner(reservation)
                pushed = _push_one(checkout)
                results.append(pushed)
                if not pushed.get("ok"):
                    # A push that failed is a repair: the phase stays waiting
                    # and no receipt is written until the push succeeds.
                    outcome = {
                        "ok": False,
                        "mode": "publish",
                        "phase": f"{int(phase):06d}",
                        "state": "RETRY",
                        "repos": results,
                        "summary": summarize([pushed], "publish"),
                        "hint": "the push did not land; the next tick retries and "
                        "no receipt is written",
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
            # and whether this publication was the group's last.
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
