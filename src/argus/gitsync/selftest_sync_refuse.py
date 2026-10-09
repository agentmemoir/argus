# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The sync checks that must refuse."""

import subprocess

from argus.gitsync.git import git, git_binary
from argus.gitsync.merge import sync_one
from argus.gitsync.selftest_fixture import (
    BOARD_REL,
    _check,
    _init_remote,
    _local_commit,
    _remote_commit,
)
from argus.gitsync.sync import sync_all


def _sync_refuse_checks(base, checks):
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
