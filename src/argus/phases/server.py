# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The server selection, and the work source the registry hands the driver."""

from pathlib import Path
from typing import Any, cast

from argus import conventions
from argus.board.reservations import (
    CoordinationError,
    CoordinationUnavailable,
    ReservationConflict,
    ReservationStore,
    reservation_file,
    save_reservation_file,
)
from argus.phases.evidence import (
    index_complete,
    is_done,
    needs_decision,
    reported_path,
    resolve_phases,
    signals,
)
from argus.phases.group import resolve_group_or_none
from argus.phases.index import audit
from argus.phases.queue import candidates, select_next
from argus.phases.runstate import run_abandoned
from argus.phases.select import _awaiting_review, _reservation_result, _select_group
from argus.select.work_source import WorkSource, WorkSourceError


def select_server_phase(
    repo: str | Path,
    machine_id: str | None,
    *,
    reserve: bool = True,
    store: Any = None,
    phases: str | Path | None = None,
) -> dict[str, Any]:
    """Select the lowest incomplete phase and claim it on the shared board.

    A future claim is intentionally ignored until it becomes the lowest
    unfinished phase.  At that point the server waits; it never jumps over a
    blocked, paused, rejected, or actively running phase.

    One exception: a phase that already carries completion evidence on disk is
    finished work, not queue work.  An operator close-out (a ticked approval
    box, a published receipt, a completed run) can leave a live claim behind,
    because the board has no state for "an operator closed this".  Gating the
    line on that stale record would strand every later phase behind a phase
    nobody is working on.  Coordination claiming ``completed`` with no evidence
    at all is still an inconsistency and still fails closed.

    A second exception, and the reason this no longer stops on an open pull
    request: a phase that has been pushed and is waiting for a human merge is
    also finished work.  It is skipped rather than offered again, and the line
    continues, because the alternative was a line that stopped for as long as
    any pull request stayed open and recovered from nothing — an abandoned pull
    request parked the whole pipeline with no way back.  The skipped phases are
    reported under ``awaiting_review`` so the driver can still run the merge
    check, which is the only thing that turns a finished pull request into a
    finished phase.
    """
    machine_id = cast("str", machine_id or conventions.setting("machine.id"))
    store = store or ReservationStore(repo)
    try:
        state, _commit = store.read()
    except CoordinationUnavailable:
        # The board's branch does not exist yet on a machine nobody has
        # migrated. Reading it may not create it -- that is what --read-only
        # means, and a read that published to the remote would make inspecting
        # the board a write. Reserving is a write, so on that path the branch is
        # created here, once, and the read is retried. A transport failure raises
        # the same error again and is reported as itself.
        if not reserve:
            raise
        store.migrate_board()
        state, _commit = store.read()
    index_done = index_complete(repo, phases)
    reviews: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        # Attached to every outcome, including the ones that do not launch, so
        # the merge check runs on a tick that selected nothing too. Absent when
        # nothing was skipped, which is the ordinary case.
        if reviews:
            result["awaiting_review"] = list(reviews)
        # Phases the selector passed over because they ended owing a decision.
        # Reported on every outcome so the driver can say so in --dry-run and a
        # person can see what the line moved past.
        if decisions:
            result["needs_decision"] = list(decisions)
        return result

    def held_answer(error: str | None = None) -> dict[str, Any]:
        """The answer for a phase the board already holds.

        A held phase may be one member of a declared reservation group. The tick
        that continues that group — or takes it over from a dead owner — reaches
        this answer rather than the group path below, because this is where a
        phase the board already holds is answered. It needs the whole array and
        the group's one branch, so the declaration travels with the refusal as it
        does on the group path. A malformed declaration fails the selection closed
        here too, for the same reason it does below.
        """
        result: dict[str, Any] = {
            "state": "WAIT_FOR_CLAIM",
            "phase": key,
            "path": reported_path(repo, path),
            "owner": record.get("machine_id"),
            "status": record.get("status"),
            "reservation_id": record.get("reservation_id"),
            "generation": record.get("generation"),
        }
        if error is not None:
            result["error"] = error
        try:
            declared = resolve_group_or_none(repo, phases, number)
        except WorkSourceError as exc:
            return {
                "state": "GROUP_UNAVAILABLE",
                "phase": key,
                "path": reported_path(repo, path),
                "error": str(exc),
            }
        if declared is not None:
            result["group"] = list(declared["group"])
        return result

    for number, path in candidates(repo, phases):
        key = f"{number:06d}"
        record = state.get("phases", {}).get(key)
        done = is_done(repo, number, path, index_done)
        # A phase whose pull request is open is finished work, not queue work,
        # so it is never offered for another run. It used to stop the line here,
        # which meant an abandoned pull request stopped every later phase for
        # ever and nothing detected that it would never be merged. The line now
        # moves on, and the next phase is cut from this one's branch so it still
        # inherits the code.
        #
        # The skipped phases ride along in the result rather than being dropped:
        # the driver uses them to run the merge check, which is the only thing
        # that turns a finished pull request into a finished phase. Skipping
        # silently would leave the receipt reading awaiting_review for ever,
        # because nothing else ever revisits it.
        #
        # Checked before, and independently of, `done`. Every phase that
        # publishes has a finished run, so a report gated on `not done` never
        # fires for a real phase — the merge check was told about nothing at all,
        # every receipt stayed awaiting_review for ever, and the next phase was
        # cut from a branch whose content was already on the integration branch.
        # That is the stall, and it was caused by this report being unreachable
        # in the only state that matters.
        review = _awaiting_review(repo, number)
        if review:
            reviews.append(
                {
                    "phase": key,
                    "branch": review.get("branch"),
                    "pull_request": review.get("pull_request"),
                    "head": review.get("head"),
                }
            )
            continue
        # A phase a person parked, or a run that ended rejected or
        # remedy_escalated, is not completion evidence and cannot resume without
        # an explicit operator action. It is reported and passed over, never
        # offered: offering it made the driver recover its own claim and launch
        # a phase glide refuses to start without --fresh, so the tick exited 2
        # and the line halted on every tick. The phase keeps its place in the
        # queue, but it is not this tick's work. Its report is appended here,
        # before the open-gate guard and before any completion `continue`, so an
        # abandoned phase with an open gate is reported rather than stalled, and
        # a run that is both complete and abandoned is still skipped but still
        # reported. The residue is the operator's own action.
        parked = run_abandoned(repo, number)
        ended = needs_decision(repo, number) if (not done or parked) else None
        if ended:
            decisions.append(
                {
                    "phase": key,
                    "path": reported_path(repo, path),
                    "state": ended,
                }
            )
        # A parked phase is the operator's own close-out: report it and move on,
        # rather than stopping the line on its still-open decision gate.
        if parked:
            continue
        # An open decision gate is reported under `needs_decision` above and
        # never blocks the run. A plan decision is the owner's work, and argus
        # does not stop the line on it.
        if record is not None:
            status = record.get("status")
            # Finished on disk, and the board is not asserting a completion we
            # can check: skip it. The claim is stale either way.
            if done and status != "completed":
                continue
            if status in {"completed", "accepted"}:
                if done:
                    continue
                # The board's completion claim has no evidence on disk. That is
                # a real inconsistency, so it still stops the line rather than
                # being skipped with the parked and terminal phases above.
                return finish(
                    {
                        "state": "COORDINATION_INCONSISTENT",
                        "phase": key,
                        "path": reported_path(repo, path),
                        "error": (
                            f"coordination says {status} but no completion "
                            "evidence exists"
                        ),
                    }
                )
            if ended:
                continue
            if status in {"claimed", "running", "paused", "blocked"}:
                return finish(held_answer())
            return finish(held_answer("explicit operator takeover is required"))
        if done:
            continue
        if ended:
            continue
        # A phase may declare a reservation group. The group is claimed as one
        # unit under one reservation id, and the answer carries the array. A
        # phase whose document declares no group is selected alone, as before,
        # so the single-phase contract is unchanged for it.
        try:
            group = resolve_group_or_none(repo, phases, number)
        except WorkSourceError as exc:
            result = {
                "state": "GROUP_UNAVAILABLE",
                "phase": key,
                "path": reported_path(repo, path),
                "error": str(exc),
            }
            document = getattr(exc, "document", None)
            if document is not None:
                result["document"] = str(document)
            return finish(result)
        if group is not None:
            selected = _select_group(
                repo,
                number,
                path,
                group,
                machine_id,
                store,
                reserve=reserve,
                reviews=reviews,
            )
            if selected is None:
                continue
            return finish(selected)
        if not reserve:
            result = _reservation_result(number, path, {}, "AVAILABLE")
            result["path"] = reported_path(repo, path)
            return finish(result)
        try:
            record = store.reserve(number, machine_id, run_id=f"phase-{key}")
        except ReservationConflict as exc:
            # The push race lost.  Read the winner rather than attempting an
            # overwrite or silently selecting a later phase.
            latest, _latest_commit = store.read()
            winner = latest.get("phases", {}).get(key, {})
            return finish(
                {
                    "state": "WAIT_FOR_CLAIM",
                    "phase": key,
                    "path": reported_path(repo, path),
                    "owner": winner.get("machine_id", exc.record.get("machine_id")),
                    "status": winner.get("status", exc.record.get("status")),
                    "reservation_id": winner.get("reservation_id"),
                    "generation": winner.get("generation"),
                }
            )
        result = _reservation_result(number, path, record)
        result["path"] = reported_path(repo, path)
        # The token is written beside the store's own checkout, never beside
        # whichever board checkout is configured. The store was handed a board
        # repository explicitly, and resolving the token's path a second time
        # from the environment is how a caller that built its own checkout
        # ended up writing a real machine's run state.
        token_path = reservation_file(repo, number)
        try:
            save_reservation_file(token_path, record)
        except OSError as exc:
            raise CoordinationError(
                f"could not persist phase reservation token: {exc}"
            ) from exc
        return finish(result)
    return finish({"state": "NO_PHASE"})


class PhasesSource(WorkSource):
    """Work from the phase files in a phases folder.

    ``select`` is the whole contract the driver depends on; the rest is the
    phases's own reporting, which the selector's diagnostic flags drive.

    An instance is built once per invocation and holds the folder it resolved,
    so no caller re-derives the layout and two parts of one tick cannot end up
    reading different folders.
    """

    name = "phases"

    def __init__(self, repo: str | Path, phases: str | Path | None = None) -> None:
        self.repo = Path(repo)
        self.phases = resolve_phases(self.repo, phases)

    def select(
        self,
        machine_id: str | None = None,
        *,
        reserve: bool = True,
        store: Any = None,
    ) -> dict[str, Any]:
        """Return the next claimable phase as a result dict, claiming it."""
        return select_server_phase(
            self.repo, machine_id, reserve=reserve, store=store, phases=self.phases
        )

    def select_next(self) -> tuple[int, Path] | None:
        """The lowest incomplete phase, or None. Never claims anything."""
        return select_next(self.repo, self.phases)

    def candidates(self) -> list[tuple[int, Path]]:
        """Every runnable phase file, lowest number first."""
        return candidates(self.repo, self.phases)

    def index_complete(self) -> set[int]:
        return index_complete(self.repo, self.phases)

    def signals(self, item: tuple[int, Path]) -> dict[str, bool]:
        return signals(self.repo, item[0], item[1], self.index_complete())

    def audit(self, apply_index: bool = False) -> int:
        return audit(self.repo, self.phases, apply_index=apply_index)
