# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One draft pull request for a branch: create it, or reuse the one that exists.

A phase pipeline asks for this mode with no arguments but the branch it already
knows, so every default here must work with no extra flags.

Three outcomes, and the mode keeps them apart:

* no pull request for the branch: create a draft, and report the number, URL,
  draft flag, and state gh returned.
* an open pull request for the branch: reuse it. The pipeline calls this on
  every tick, so a second call must never open a second draft for the same
  commits.
* the newest pull request for the branch is merged or closed: refuse with
  ``state: blocked`` and name that pull request. New work on a branch whose
  pull request already closed is the reviewer's decision, not this mode's.

A gh read that fails is never read as "no pull request". Believing that would
create a duplicate on every network blip, so an unreadable list stops the mode.
"""

from pathlib import Path
from typing import Any

from argus import conventions
from argus.gitsync.gh import gh_create_draft_pull_request, gh_pull_requests_for_branch
from argus.gitsync.git import (
    ConfigError,
    current_branch,
    integration_branch,
    summarize,
)
from argus.gitsync.paths import phase_branch


def _resolve_branch(root: Path, phase: int | str | None, branch: str | None) -> str:
    """The branch to open the pull request from.

    An explicit branch wins; then the phase's own branch, derived from its phases
    file; then the branch the checkout is on. Nothing is guessed: a phase with no
    phases file, or a detached HEAD with no branch named, is a configuration
    error naming what is missing, so a wrong call fails in seconds.

    Raises:
        ConfigError: No branch could be resolved.
    """
    if branch:
        return str(branch)
    if phase is not None:
        number = int(phase)
        found = conventions.resolve_phase_file(root, number)
        if found is None:
            raise ConfigError(
                f"--phase {number:06d} has no phases file in the configured "
                "phases folder; pass --branch"
            )
        return phase_branch(found.name)
    here = current_branch(root)
    if not here:
        raise ConfigError(
            "no --branch, no --phase, and the checkout is on a detached HEAD; "
            "pass --branch or --phase"
        )
    return here


def _text_from_file(path: str | Path | None, flag: str) -> str | None:
    """The text a caller put in a file, or None when no file was named.

    Raises:
        ConfigError: The file is missing or empty. Sending gh a title or body the
            caller did not write would publish a pull request nobody asked for.
    """
    if path is None:
        return None
    source = Path(path)
    if not source.is_file():
        raise ConfigError(f"{flag} {str(source)!r} does not exist")
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise ConfigError(f"{flag} {str(source)!r} is empty")
    return text


def _number_from_url(url: str) -> int | None:
    """The pull request number gh put at the end of its URL, when there is one."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _result(branch: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The mode's result, in the shape every other mode returns."""
    ok = bool(rows) and all(bool(row.get("ok")) for row in rows)
    return {
        "ok": ok,
        "mode": "open-pr",
        "branch": branch,
        "repos": rows,
        "summary": summarize(rows, "open-pr"),
    }


def _row(root: Path) -> dict[str, Any]:
    """One repository's row, with every field present and unclaimed."""
    return {
        "repo": str(root),
        "ok": False,
        "action": None,
        "number": None,
        "url": None,
        "draft": None,
        "state": None,
    }


def mode_open_pr(
    root: str | Path,
    phase: int | str | None = None,
    branch: str | None = None,
    title_file: str | Path | None = None,
    body_file: str | Path | None = None,
) -> dict[str, Any]:
    """Open a draft pull request for a branch, or reuse the one that exists.

    The title defaults to the branch name and the body to empty, which is what
    the pipeline's own call gets. ``title_file`` and ``body_file`` let a caller
    supply its own text, and a file that is missing or empty is refused rather
    than quietly replaced by the default.

    Returns the mode's result dict: ``ok``, ``mode``, ``branch``, ``repos``, and
    ``summary``, with ``state: blocked`` and a blocker naming the reason when the
    mode could not finish.
    """
    root = Path(root).resolve()
    branch_name = _resolve_branch(root, phase, branch)
    title = _text_from_file(title_file, "--title-file")
    body = _text_from_file(body_file, "--body-file")
    row = _row(root)

    found, why = gh_pull_requests_for_branch(root, branch_name)
    if found is None:
        # Could not read. Not the same as "no pull request exists", so nothing is
        # created on a guess.
        row["blockers"] = [f"could not list pull requests for {branch_name!r}: {why}"]
        out = _result(branch_name, [row])
        out["state"] = "blocked"
        return out

    open_rows = [item for item in found if item["state"] == "OPEN"]
    if open_rows:
        newest = max(open_rows, key=lambda item: int(item["number"]))
        row.update(
            ok=True,
            action="reused",
            number=int(newest["number"]),
            url=newest["url"],
            draft=bool(newest["isDraft"]),
            state="OPEN",
        )
        return _result(branch_name, [row])

    if found:
        newest = max(found, key=lambda item: int(item["number"]))
        state = str(newest["state"])
        row.update(
            number=int(newest["number"]),
            url=newest["url"],
            draft=bool(newest["isDraft"]),
            state=state,
        )
        row["blockers"] = [
            f"the newest pull request for {branch_name!r} is {state}, "
            f"#{int(newest['number'])} {newest['url']}; not opening a duplicate"
        ]
        out = _result(branch_name, [row])
        out["state"] = "blocked"
        return out

    created, detail = gh_create_draft_pull_request(
        root,
        branch_name,
        integration_branch(root),
        title=title,
        body=body,
    )
    if created is None:
        row["blockers"] = [
            f"gh could not create a draft pull request for {branch_name!r}: {detail}"
        ]
        out = _result(branch_name, [row])
        out["state"] = "blocked"
        return out
    url = created.strip()
    # gh printed the new pull request's URL and exited 0. It was asked for as a
    # draft, so both of those are what gh reported, not what this mode hoped.
    row.update(
        ok=True,
        action="created",
        number=_number_from_url(url),
        url=url,
        draft=True,
        state="OPEN",
    )
    return _result(branch_name, [row])


__all__ = ["mode_open_pr"]
