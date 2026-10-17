# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Self-test checks for the board branch and the state file."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from argus.board.branch import _backup_branch, _integration_branch
from argus.board.core import SELF_TEST_BRANCH, CoordinationUnavailable
from argus.board.git import _git, git_binary
from argus.board.selftest_fixture import _git_test_repo, _self_test_check
from argus.board.state import state_rel
from argus.board.store import ReservationStore


def _board_branch_checks(checks: list[dict[str, Any]], base: Path) -> None:
    """The board gets its own branch, and moving it must not move run state.

    The defect this replaces was a board commit landing on the branch a phase's
    own commits are based on, so publishing the phase raced the bookkeeping. The
    property to pin is that a reservation leaves the integration branch exactly
    where it was, and that a phase's own push still succeeds on either side of a
    board commit.
    """
    remote, clients = _git_test_repo(base, "board-branch")
    private = clients[0]
    root = base / "board-root"
    root.mkdir()

    def integration_tip(repo: Path) -> str | None:
        rc, commit, _err = _git(
            repo,
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{_integration_branch(repo, 'origin')}",
        )
        return commit.strip() if rc == 0 else None

    # The store reads its branch from the fixture settings. The board also has
    # to use a branch other than the integration branch, because a reservation
    # cannot leave that branch unchanged when the two branches are the same.
    store = ReservationStore(root, private_repo=private)
    _self_test_check(
        checks,
        "the board uses its configured branch",
        store.branch == SELF_TEST_BRANCH,
        store.branch,
    )
    _self_test_check(
        checks,
        "the board's branch is not the integration branch",
        _integration_branch(private, "origin") != SELF_TEST_BRANCH,
        f"{SELF_TEST_BRANCH} vs {_integration_branch(private, 'origin')}",
    )

    # Reading the board must not be able to change the remote. Seeding used to
    # hang off the fetch, so the first read on a machine that had not migrated
    # published a branch, which is a write on the path that exists precisely so a
    # caller can inspect without writing. The check names both halves: the read
    # refuses, and the remote's branch list is untouched afterwards.
    #
    # The branch is named rather than taken from the store, because the branch a
    # store uses is the configured value, and the fixtures that run this
    # suite deliberately name one that already exists. A probe for "the board
    # branch is missing" has to use a branch that is missing.
    def remote_branches() -> list[str] | None:
        rc, out, _err = _git(remote, "branch", "--format=%(refname:short)")
        return sorted(out.split()) if rc == 0 else None

    probe = ReservationStore(root, private_repo=private, branch="fixture-probe")

    # The seed source is the integration branch's tip, whatever the private
    # checkout is sitting on. A phase run puts that checkout on its own feature
    # branch, so seeding from its HEAD would root every machine's shared board
    # history at whichever commit that phase happened to start from. The fixture
    # makes the two candidates different commits so the check can tell them apart:
    # a fetch-then-resolve answer and a HEAD answer are different commits.
    case = base / "seed-source"
    case.mkdir()
    seed_remote, seed_clients = _git_test_repo(case, "seed-source")
    seed_private = seed_clients[0]
    seed_root = case / "root"
    seed_root.mkdir()
    (seed_private / "integration.txt").write_text("integration\n")
    _git(seed_private, "add", "-A")
    _git(seed_private, "commit", "-q", "-m", "integration work")
    _git(
        seed_private,
        "push",
        "-q",
        "origin",
        f"HEAD:refs/heads/{_integration_branch(seed_private, 'origin')}",
    )
    integration_tip_sha = _git(
        seed_remote,
        "rev-parse",
        f"refs/heads/{_integration_branch(seed_private, 'origin')}",
    )[1].strip()
    _git(seed_private, "checkout", "-q", "-b", "phase-100712-feature")
    (seed_private / "feature.txt").write_text("feature\n")
    _git(seed_private, "add", "-A")
    _git(seed_private, "commit", "-q", "-m", "feature work")
    feature_tip = _git(seed_private, "rev-parse", "HEAD")[1].strip()
    # The stale remote-tracking ref is deleted, because it is what makes the
    # question real: with it present, a `rev-parse` answers the integration tip
    # and no code has to choose anything. A clone that has never fetched the
    # integration branch holds no ref for it, and that is the machine this is
    # about. Fetching before asking is what answers it; asking the checkout is
    # what would answer wrongly.
    _git(
        seed_private,
        "update-ref",
        "-d",
        f"refs/remotes/origin/{_integration_branch(seed_private, 'origin')}",
    )
    seed_store = ReservationStore(
        seed_root, private_repo=seed_private, branch="coordination-seed-source"
    )
    seed_store.migrate_board()
    rc, seeded, _err = _git(
        seed_remote, "rev-parse", "refs/heads/coordination-seed-source"
    )
    seeded_tip = seeded.strip() if rc == 0 else ""
    _self_test_check(
        checks,
        "the board is seeded from the integration tip, not the checkout's branch",
        bool(seeded_tip)
        and seeded_tip == integration_tip_sha
        and integration_tip_sha != feature_tip,
        f"seeded {seeded_tip[:12]}; integration {integration_tip_sha[:12]}; "
        f"on-branch tip {feature_tip[:12]}",
    )
    shutil.rmtree(case, ignore_errors=True)

    before_read = remote_branches()
    refused = ""
    try:
        probe.read()
        refused = "the read succeeded against a remote with no board branch"
    except CoordinationUnavailable as exc:
        refused = str(exc).splitlines()[0][:110]
    after_read = remote_branches()
    _self_test_check(
        checks,
        "a read refuses rather than creating the board branch",
        "does not exist" in refused and before_read == after_read,
        f"{refused}; branches {before_read} -> {after_read}",
    )

    created = probe.migrate_board()
    _self_test_check(
        checks,
        "the named migration step creates the board branch",
        created and remote_branches() != before_read,
        f"created={created}; branches {remote_branches()}",
    )
    _self_test_check(
        checks,
        "migrating twice is the same answer as migrating once",
        probe.migrate_board() is False,
        "a second migration creates nothing",
    )

    before = integration_tip(private)
    record = store.reserve(100710, "server-01")
    after = integration_tip(private)
    _self_test_check(
        checks,
        "a reservation does not move the integration branch",
        before is not None and before == after,
        f"{str(before)[:12]} -> {str(after)[:12]}",
    )

    rc, state, _err = _git(
        private, "show", f"origin/{store.branch}:{state_rel().as_posix()}"
    )
    held = False
    if rc == 0:
        held = (
            json.loads(state).get("phases", {}).get("100710", {}).get("reservation_id")
            == record["reservation_id"]
        )
    _self_test_check(
        checks,
        "the board's own branch holds the reservation",
        held,
        f"origin/{store.branch}",
    )

    backup_ref = f"origin/{_backup_branch(store.branch)}"
    rc, state, _err = _git(private, "show", f"{backup_ref}:{state_rel().as_posix()}")
    backed_up = False
    if rc == 0:
        backed_up = (
            json.loads(state).get("phases", {}).get("100710", {}).get("reservation_id")
            == record["reservation_id"]
        )
    _self_test_check(
        checks,
        "the backup ref holds the same reservation",
        backed_up,
        backup_ref,
    )

    # A phase's own commit and a board commit are now siblings on two branches,
    # so neither can be rejected as non-fast-forward by the other. Both orders
    # are exercised, because the race only ever went one way.
    for attempt in ("board-then-phase", "phase-then-board"):
        case = base / f"race-{attempt}"
        case.mkdir()
        _race_remote, race_clients = _git_test_repo(case, attempt)
        race_private = race_clients[0]
        race_root = case / "root"
        race_root.mkdir()
        (race_root / "runs" / "phase-100711").mkdir(parents=True)
        (race_private / "runs" / "phase-100711").mkdir(parents=True)
        phase_store = ReservationStore(
            race_root, private_repo=race_private, branch=SELF_TEST_BRANCH
        )
        integration = _integration_branch(race_private, "origin")

        def publish_run_state(
            race_private: Path = race_private,
            integration: str = integration,
        ) -> bool:
            # Run state is published from the private checkout, which is where
            # the publishing tool writes it.
            (race_private / "runs" / "phase-100711" / "run.json").write_text(
                json.dumps({"state": "completed"}) + "\n"
            )
            _git(race_private, "add", "-A")
            _git(race_private, "commit", "-q", "-m", "run state")
            return (
                _git(race_private, "push", "origin", f"HEAD:refs/heads/{integration}")[
                    0
                ]
                == 0
            )

        if attempt == "board-then-phase":
            phase_store.reserve(100711, "server-01")
            phase_ok = publish_run_state()
        else:
            phase_ok = publish_run_state()
            phase_store.reserve(100711, "server-01")
        _self_test_check(
            checks,
            f"a phase's own push survives a board commit ({attempt})",
            phase_ok,
            f"pushed to {integration}: {phase_ok}",
        )
        shutil.rmtree(case, ignore_errors=True)


def _deleted_state_check(base: Path) -> tuple[bool, str]:
    remote, clients = _git_test_repo(base, "deleted-state")
    root = base / "deleted-root"
    root.mkdir()
    store = ReservationStore(root, private_repo=clients[0], branch=SELF_TEST_BRANCH)
    store.reserve(100440, "server-01")
    deleter = base / "deleted-client"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(deleter)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(deleter, "config", "user.email", "coordination-test@example.com")
    _git(deleter, "config", "user.name", "coordination test")
    # The deletion has to happen on the board's own branch. Deleting the file on
    # the integration branch used to be the same thing, and is now a change to a
    # file nothing reads.
    _git(deleter, "checkout", "-q", "-B", store.branch, f"origin/{store.branch}")
    _git(deleter, "rm", "-q", state_rel().as_posix())
    _git(deleter, "commit", "-q", "-m", "delete coordination state")
    _git(deleter, "push", "origin", f"HEAD:refs/heads/{store.branch}")
    try:
        store.read()
        return False, "deleted state was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


def _bad_phase_key_check(base: Path) -> tuple[bool, str]:
    bad: dict[str, Any] = {
        "version": 1,
        "phases": {
            "1": {
                "status": "running",
                "machine_id": "server-01",
                "reservation_id": "rsv-bad-key",
                "generation": 1,
                "claimed_at": "x",
                "heartbeat_at": "x",
                "expires_at": None,
                "run_id": "phase-1",
                "base_revisions": {},
            }
        },
    }
    _remote, clients = _git_test_repo(base, "bad-key", initial_state=bad)
    root = base / "bad-key-root"
    root.mkdir()
    store = ReservationStore(root, private_repo=clients[0])
    try:
        store.read()
        return False, "noncanonical phase key was accepted"
    except CoordinationUnavailable as exc:
        return True, str(exc)


__all__ = ["_bad_phase_key_check", "_board_branch_checks", "_deleted_state_check"]
