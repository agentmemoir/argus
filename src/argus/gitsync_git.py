# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The git primitives: one command runner, the remote and upstream facts, and the sync summary."""

import os
import subprocess
import urllib.parse
from pathlib import Path

from argus.conventions import board_repo, setting

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


def summarize(results, mode):
    parts = []
    for r in results:
        if r.get("ok"):
            parts.append(f"{r['repo']}: {r.get('action', 'ok')}")
        else:
            parts.append(f"{r['repo']}: BLOCKED ({'; '.join(r.get('blockers', []))})")
    head = f"{mode} sync {'ok' if all(r.get('ok') for r in results) else 'BLOCKED'}"
    return f"{head}: " + "; ".join(parts)
