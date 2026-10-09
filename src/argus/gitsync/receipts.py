# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Receipts, phase run entries, pull request URLs, and the branch facts around them."""

import json
import re
import subprocess

from argus.conventions import (
    phase_file_parts,
    phase_paths,
    phase_run_dir,
    runs_dir,
    setting,
)
from argus.gitsync.git import GH_TIMEOUT_S, gh_binary, git
from argus.gitsync.paths import roadmap_folder


def _published_head(results, repo):
    for result in results:
        if result.get("repo") == str(repo) and result.get("action") == "pushed":
            return result.get("after")
    return None


def phase_receipt(repo, number):
    """A phase's publication receipt, or None when there is none.

    A receipt is the only record of where a phase's commits ended up, so it is
    read by the selector to decide whether the phase is still waiting for a
    human. A receipt that does not parse is treated as absent, not as
    completion: an unreadable receipt must never read as a merged phase.
    """
    path = phase_run_dir(repo, number) / "publication.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def head_tree_on_integration(checkout, head, integration):
    """Whether ``head``'s file content is already on the integration branch.

    A squash merge deletes the commit a phase pushed, so the commit-id test
    cannot see it. What survives is the content: the squash commit's tree is
    the phase branch's tree, so the phase's work is present on the integration
    branch under a different commit id.

    This is the proof that still works when GitHub cannot be asked at all, which
    is the case that matters most — a missing or unauthenticated ``gh`` must
    degrade to a working answer, not to a line that never starts again.

    Returns True or False. False means "not found", which is the safe answer:
    the caller then falls back to the branch itself, so a phase that really was
    merged is never mistaken for one that was not.
    """
    rc, tree, _ = git(checkout, "rev-parse", "--verify", f"{head}^{{tree}}")
    if rc != 0 or not tree:
        return False
    rc, _, _ = git(checkout, "cat-file", "-e", f"{integration}^{{tree}}")
    if rc != 0:
        return False
    # The integration branch's own tip, checked first because it is the common
    # case and costs one comparison rather than a walk.
    rc, tip_tree, _ = git(checkout, "rev-parse", "--verify", f"{integration}^{{tree}}")
    if rc == 0 and tip_tree == tree:
        return True
    # Otherwise the squash commit's tree is somewhere in the branch's history.
    rc, found, _ = git(checkout, "log", "--format=%T", "-n", "200", integration)
    return rc == 0 and tree in found.split()


def _run_phase_number(entry):
    parts = phase_file_parts(entry.name + ".md")
    if parts is not None:
        return int(parts[0])
    match = re.fullmatch(r"phase-(\d+)", entry.name)
    if match:
        return int(match.group(1))
    try:
        receipt = json.loads((entry / "publication.json").read_text())
        return int(receipt["phase"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _phase_run_entries(root):
    if setting("pipeline", None) is not None:
        roadmap = roadmap_folder(root)
        for phase_file in phase_paths(roadmap):
            parts = phase_file_parts(phase_file)
            if parts is not None:
                entry = phase_run_dir(root, parts[0], phase_file)
                if entry.is_dir():
                    yield parts[0], entry
        return
    try:
        entries = sorted(runs_dir(root).iterdir())
    except OSError:
        return
    for entry in entries:
        number = _run_phase_number(entry)
        if number is not None and entry.is_dir():
            yield number, entry


# A pull request URL, as finalize records it. Only the four segments GitHub
# guarantees are captured: scheme, owner, repository, and the number under
# /pull/. A trailing path, a query, or a fragment is tolerated, because
# finalize pastes whatever URL the reviewer is looking at, and a URL the
# pipeline cannot parse is stored whole with no number so that
# ``gh_pull_request_state`` is simply not consulted.
#
# host is deliberately not pinned to github.com: GitHub Enterprise installs
# this pipeline too, and the host is passed through rather than matched. What
# the number is checked against is the receipt that named the URL, so a URL
# from another host still only ever confirms the phase's own pull request.
_PULL_REQUEST_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


def pull_request_ref(url):
    """The (host, owner, repo, number) a pull request URL names, or None.

    Args:
        url: A pull request URL, or anything at all.

    Returns:
        A dict with ``host``, ``owner``, ``repo`` and ``number`` (an int), or
        None when the string is not a pull request URL. None is the normal
        answer for an empty or malformed value, not an error: the caller stores
        the URL for humans either way and falls back to the commit-id test.
    """
    match = _PULL_REQUEST_URL_RE.match(str(url or "").strip())
    if not match:
        return None
    return {
        "host": match.group("host"),
        "owner": match.group("owner"),
        "repo": match.group("repo"),
        "number": int(match.group("number")),
    }


def gh_pull_request_state(number, owner, repo, host=None):
    """Ask GitHub whether a pull request is merged.

    Returns ``(state, detail)`` where state is ``merged``, ``not-merged``, or
    ``unknown``. Three states, not two: a pipeline whose answer is binary
    cannot tell "GitHub says not merged" from "GitHub could not be reached",
    and only one of those is an answer. ``unknown`` must never be read as
    merged, so every caller falls back to the commit-id test, which fails
    closed.

    ``gh`` is invoked rather than a hand-rolled HTTP call because ``gh`` already
    holds the credential in the host keychain and keeps it out of every log,
    file, and command line, which is the whole reason ``github_issues.py``
    exists. A second token path is a second thing to audit.

    Its stderr is captured and discarded rather than reported. An
    unauthenticated call prints the host account name into the message, and run
    logs are copied into issues; the exit status is what this needs and the
    status alone is enough to separate "not merged" from "could not ask".
    """
    target = f"{owner}/{repo}"
    if host and host not in ("github.com",):
        target = f"{host}/{target}"
    argv = [
        gh_binary(),
        "pr",
        "view",
        str(int(number)),
        "--repo",
        target,
        "--json",
        "state,mergedAt,mergeCommit",
    ]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        )
    except FileNotFoundError:
        return "unknown", "gh is not installed"
    except subprocess.TimeoutExpired:
        return "unknown", f"gh did not answer within {GH_TIMEOUT_S}s"
    if done.returncode != 0:
        # Exit status only, never the message. 1 is a general failure and 4 is
        # "authentication required"; both mean the same thing here, and neither
        # may be reported as a merge.
        return "unknown", f"gh exited {done.returncode}"
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return "unknown", "gh did not return JSON"
    if not isinstance(data, dict):
        return "unknown", "gh returned JSON that is not an object"
    state = str(data.get("state") or "")
    if state == "MERGED":
        # mergeCommit, not mergedAt, is the durable record: it names the commit
        # GitHub created, and it is populated for a rebase merge where mergedAt
        # is not. Both are read defensively because a field's shape in gh's JSON
        # is gh's, not GitHub's, and this pipeline cannot exercise it against a
        # live pull request without a token it must not reach for.
        commit = data.get("mergeCommit")
        oid = (
            commit
            if isinstance(commit, str)
            else (commit.get("oid") if isinstance(commit, dict) else None)
        )
        return "merged", oid or str(data.get("mergedAt") or "")
    if state in {"OPEN", "CLOSED"}:
        # CLOSED is a closed-without-merging pull request, which is a real
        # answer and not a pending one. Either way it is not merged.
        return "not-merged", state
    return "unknown", f"unrecognised state {state!r}"


def _head_carried_locally(checkout, head, candidates):
    """The first candidate ref in this checkout that carries ``head``, or None.

    Split out so the "try local first, fetch only if that failed" rule is one
    named thing rather than a loop that has to be read twice to see whether a
    fetch happened.
    """
    for candidate in candidates:
        if not candidate:
            continue
        rc, _, _ = git(checkout, "merge-base", "--is-ancestor", head, candidate)
        if rc == 0:
            return candidate
    return None


def receipt_for_branch(repo, branch):
    """The publication receipt whose ``branch`` is ``branch``, or None.

    The branch name is the join key, not the phase number, because the branch
    no longer carries the number. Scanning every run directory is deliberate:
    a receipt is the only evidence that a phase reached review, and a phase the
    operator has only written in the roadmap has none.
    """
    for _number, entry in _phase_run_entries(repo):
        try:
            data = json.loads((entry / "publication.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("branch") == branch:
            return data
    return None
