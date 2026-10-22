# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The git primitives: one command runner, the remote and upstream facts, and the sync summary."""

import os
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

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
# git names the local user and machine in ``'<user>@<host>.(none)'`` when it
# cannot find an identity, so its stderr is read for a known cause and then
# dropped: SECURITY.md rule 4 forbids storing a machine name in a warning.
_NO_IDENTITY_HINTS = (
    "unable to auto-detect email",
    "please tell me who you are",
    "empty ident",
)


def _git_failure(step: str, rc: int, stderr: str) -> str:
    """Name a failed git step by step and exit code, never by its stderr.

    ``stderr`` is read for the one cause worth naming to a reader, a checkout
    with no git identity, and is not carried into the result.
    """
    if any(hint in stderr.lower() for hint in _NO_IDENTITY_HINTS):
        return f"{step} failed (git exit {rc}): git has no user identity"
    return f"{step} failed (git exit {rc})"


# A degraded part carries the start of its warning into a log line, so the
# excerpt is bounded: a git message can be many lines long.
WARNING_EXCERPT_CHARS = 120

# The integration branch: the one a phase's pull request targets, and the one
# nothing a phase writes may land on. Resolved per checkout rather than
# hard-coded, because a repository's integration branch is a fact about that
# repository and this value is only the expected name for it.
INTEGRATION_BRANCH = "master"


def integration_branch(repo: str | Path, default: str = INTEGRATION_BRANCH) -> str:
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


def git_binary() -> str:
    """The git command named in the loaded config."""
    return setting("commands.git")


def gh_binary() -> str:
    """The gh command named in the loaded config."""
    return setting("commands.gh")


def git(
    repo: str | Path,
    *args: str,
    timeout: int = GIT_TIMEOUT_S,
    extra_env: dict[str, str] | None = None,
    raw: bool = False,
) -> tuple[int, str, str]:
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


def push_moved(porcelain: str, branch: str) -> bool:
    """Whether a ``git push --porcelain`` moved ``branch``'s own remote ref.

    Each status line is ``<flag>\\t<from>:<to>\\t<summary> (<reason>)``, and
    ``=`` marks a ref that was up to date and did not need pushing. Only the
    line whose destination is this branch answers: a push also carries
    annotated tags when ``push.followTags`` is set, and a moving tag is not a
    moved branch. A push that reported no line for the branch moved nothing
    for it; plain lines such as ``To <remote>`` and ``Done`` carry no tab and
    are not status lines.
    """
    wanted = f"refs/heads/{branch}"
    for line in porcelain.splitlines():
        flag, tab, rest = line.partition("\t")
        if not tab:
            continue
        refs, _tab, _summary = rest.partition("\t")
        _source, colon, target = refs.partition(":")
        if colon and target == wanted and flag != "=":
            return True
    return False


def is_git_repo(path: str | Path | None) -> bool:
    if path is None:
        return False
    rc, out, _ = git(path, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out == "true"


def git_toplevel(path: str | Path) -> Path | None:
    """The git working tree that contains ``path``, or ``None``.

    ``path`` may not exist yet: a phase run directory is named before it is
    created, and the publish helper resolves it first. The search therefore
    starts at the nearest existing ancestor. ``--show-toplevel`` reports the
    innermost work tree, so a run directory inside a nested checkout maps to
    that checkout rather than to its enclosing repository.
    """
    candidate = Path(path).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    rc, out, _ = git(candidate, "rev-parse", "--show-toplevel")
    if rc != 0 or not out:
        return None
    return Path(out).resolve()


def resolve_upstream(
    repo: str | Path,
) -> tuple[str | None, str | None, str | None, str | None]:
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


def remote_urls(repo: str | Path, remote: str, *, push: bool = False) -> list[str]:
    args = ["remote", "get-url"]
    if push:
        args.append("--push")
    args.extend(["--all", remote])
    rc, out, _ = git(repo, *args)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def canonical_remote_url(value: str | None) -> str:
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


def configured_remote_urls(repo: str | Path, *, push: bool = False) -> set[str]:
    """Every fetch (or push) URL configured by the checkout's remotes.

    The public/private separation proof reads these rather than the current
    branch's upstream. Separation is a property of the two repositories'
    configured destinations, and a checkout parked on a branch whose
    remote-tracking ref was deleted still has them; reading the branch's
    upstream instead answered with an empty set and refused the tick with
    "cannot prove separate destinations" for a branch that had merely lost its
    remote copy.
    """
    rc, out, _ = git(repo, "remote")
    if rc != 0:
        return set()
    urls: set[str] = set()
    for name in out.splitlines():
        if name.strip():
            urls.update(
                canonical_remote_url(url) for url in remote_urls(repo, name, push=push)
            )
    return urls


def classify(repo: str | Path, upstream: str) -> tuple[str, str, int, int, str]:
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


def subjects(repo: str | Path, rev_range: str) -> list[str]:
    rc, out, _ = git(
        repo, "log", "--oneline", "--no-decorate", f"-n{MAX_SUBJECTS}", rev_range
    )
    return out.splitlines() if rc == 0 and out else []


def incoming_paths(repo: str | Path, upstream: str) -> list[str]:
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


def overlapping_paths(repo: str | Path, upstream: str, local: list[str]) -> list[str]:
    """The locally-dirty paths the incoming commits also change."""
    incoming = set(incoming_paths(repo, upstream))
    return sorted(incoming & set(local))


def blocked(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    info = dict(info)
    info["ok"] = False
    info["action"] = "blocked"
    blockers = list(info.get("blockers", []))
    blockers.append(reason)
    info["blockers"] = blockers
    info.update(extra)
    return info


def degraded(info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    """Report a step that did not happen, and let the caller continue.

    This is the repair form of :func:`blocked`: the work is reported in
    ``warning`` so the next tick can retry it, and ``ok`` stays true so no
    caller halts the line. A narrow lost-work stop still uses ``blocked``.

    ``ok`` staying true is the retry design, not a claim of success: the
    warning is carried into :func:`summarize`, so a line that continues is
    logged as ``DEGRADED`` and names the step that was deferred.
    """
    info = dict(info)
    info["ok"] = True
    info["action"] = "continued"
    info["warning"] = reason
    info.update(extra)
    return info


def dirty_paths(repo: str | Path) -> list[str]:
    """Repo-relative paths with any staged, unstaged, or untracked change.

    Ignored files (for example the runner's interface log and driver state) do not
    appear, so this is exactly the set a commit would carry."""
    _, out, _ = git(repo, "status", "--porcelain=v1", "--untracked-files=all", raw=True)
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:  # renames: "XY old -> new"
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return paths


def _is_private_path(root: str | Path, path: str) -> bool:
    """Exclude only the configured nested private checkout from public dirt."""
    private = board_repo(root)
    if private is None:
        return False
    try:
        prefix = str(private.relative_to(Path(root).resolve()))
    except ValueError:
        return False
    return path == prefix or path.startswith(prefix + "/")


def is_run_state(path: str, scope: str = "runs") -> bool:
    """True for the pipeline's own run-state paths inside the private repo."""
    return path == scope or path.startswith(scope.rstrip("/") + "/")


def current_branch(repo: str | Path) -> str:
    """The checked-out branch, or an empty string on a detached HEAD."""
    rc, out, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out if rc == 0 and out and out != "HEAD" else ""


def _warning_excerpt(warning: str) -> str:
    """The first line of a warning, cut to a length one log line can hold."""
    first = warning.splitlines()[0].strip() if warning.splitlines() else ""
    if len(first) <= WARNING_EXCERPT_CHARS:
        return first
    return first[:WARNING_EXCERPT_CHARS].rstrip() + "..."


def summarize(results: list[dict[str, Any]], mode: str) -> str:
    """One line per repo, and a head word that tells a continued line from a clean one.

    A degraded result keeps ``ok`` true so the next tick retries it, which by
    itself reads exactly like a clean line. Both the head word and the repo's
    own part therefore say so: the head is ``DEGRADED`` and the part carries
    the start of the warning.
    """
    parts: list[str] = []
    for r in results:
        # A stop is printed as a stop, even when the same result also carries a
        # deferred step: its blockers are the news, the warning is not.
        if not r.get("ok"):
            parts.append(f"{r['repo']}: BLOCKED ({'; '.join(r.get('blockers', []))})")
            continue
        warning = str(r.get("warning") or "")
        if warning:
            excerpt = _warning_excerpt(warning)
            parts.append(
                f"{r['repo']}: continued ({excerpt})"
                if excerpt
                else f"{r['repo']}: continued"
            )
        else:
            parts.append(f"{r['repo']}: {r.get('action', 'ok')}")
    # A blocked result outranks a degraded one: nothing it deferred is worth
    # reporting next to a stop.
    if not all(r.get("ok") for r in results):
        verdict = "BLOCKED"
    elif any(r.get("warning") for r in results):
        verdict = "DEGRADED"
    else:
        verdict = "ok"
    return f"{mode} sync {verdict}: " + "; ".join(parts)


__all__ = ["_is_private_path", "degraded", "git_toplevel"]
