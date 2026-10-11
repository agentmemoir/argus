# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The sync_one checks: fast-forward, merge, refusal."""

import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.git import git, git_binary
from argus.gitsync.merge import sync_one
from argus.gitsync.selftest_fixture import (
    _check,
    _init_remote,
    _local_commit,
    _remote_commit,
)


def _sync_one_checks(base: Path, checks: list[dict[str, Any]]) -> None:
    # 1. stale start is synced by fast-forward, preserving the remote work.
    d = base / "stale"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    _remote_commit(remote, d, "w")
    res: dict[str, Any] = sync_one(work, "start")
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

    # 6g. A branch whose remote-tracking ref was pruned locally, while the
    # remote still has the branch: start sync fetches the configured remote,
    # recreates the ref, and fast-forwards instead of refusing with no-upstream.
    d = base / "pruned-tracking-ref"
    d.mkdir()
    remote, work = _init_remote(d, "w")
    git(work, "checkout", "-q", "-b", "feature/pruned")
    git(work, "push", "-q", "-u", "origin", "feature/pruned")
    other = d / "other-pruned"
    subprocess.run(
        [git_binary(), "clone", "-q", str(remote), str(other)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(other, "config", "user.email", "sync-test@example.com")
    git(other, "config", "user.name", "sync test")
    git(other, "checkout", "-q", "feature/pruned")
    (other / "c.txt").write_text("remote\n")
    git(other, "add", "c.txt")
    git(other, "commit", "-q", "-m", "advance feature/pruned")
    git(other, "push", "-q", "origin", "feature/pruned")
    # Only the local remote-tracking ref goes; the remote branch stays.
    git(work, "update-ref", "-d", "refs/remotes/origin/feature/pruned")
    res = sync_one(work, "start")
    _check(
        checks,
        "sync: a pruned tracking ref is recovered by a fetch",
        res["ok"]
        and res["action"] == "fast-forwarded"
        and (work / "c.txt").read_text() == "remote\n",
        f"action={res.get('action')} blockers={res.get('blockers')}",
    )


__all__ = ["_sync_one_checks"]
