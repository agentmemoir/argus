# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Open a phase draft and record a single CI read at step boundaries."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.conventions import phase_run_dir
from argus.gitsync.gh import gh_create_draft_pull_request
from argus.gitsync.gh_gate import gh_pull_request_checks
from argus.gitsync.git import (
    GH_TIMEOUT_S,
    ConfigError,
    current_branch,
    gh_binary,
    git,
    integration_branch,
)
from argus.gitsync.group_gate import checks_verdict
from argus.gitsync.receipts import pull_request_ref


def _open_pull_request(root: Path, branch: str) -> dict[str, Any] | None:
    try:
        done = subprocess.run(
            [
                gh_binary(),
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number,url",
                "--limit",
                "2",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise ConfigError("gh is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConfigError("gh did not answer in time") from exc
    if done.returncode:
        raise ConfigError(f"gh exited {done.returncode}")
    try:
        rows = json.loads(done.stdout)
    except ValueError as exc:
        raise ConfigError("gh did not return JSON") from exc
    if not isinstance(rows, list) or len(rows) > 1:
        raise ConfigError("gh returned an unexpected pull request list")
    if not rows:
        return None
    if not isinstance(rows[0], dict) or not pull_request_ref(rows[0].get("url")):
        raise ConfigError("gh returned an invalid pull request")
    return dict(rows[0])


def mode_pr(root: str | Path, phase: str | int, mode: str) -> dict[str, Any]:
    """Open the branch's draft, or save one CI snapshot without waiting."""
    root = Path(root).resolve()
    branch = current_branch(root)
    base = integration_branch(root)
    if not branch or branch == base:
        raise ConfigError("a phase branch is required")
    pr = _open_pull_request(root, branch)
    out: dict[str, Any] = {"ok": True, "mode": mode, "branch": branch}
    if mode == "open-pr" and pr is None:
        rc, ahead, _ = git(root, "rev-list", "--count", f"{base}..HEAD")
        if rc or not ahead.isdigit():
            raise ConfigError("could not compare phase branch with base")
        if int(ahead) == 0:
            return dict(out, state="NOT_AHEAD")
        url, why = gh_create_draft_pull_request(root, branch, base)
        if url is None:
            raise ConfigError(why)
        ref = pull_request_ref(url)
        if ref is None:
            raise ConfigError("gh returned an invalid pull request URL")
        pr = {"number": ref["number"], "url": url}
    if mode == "open-pr":
        out.update(state="DRAFT", pull_request=pr)
        filename = "pull-request.json"
    else:
        filename = "ci-peek.json"
        ref = pull_request_ref(pr.get("url")) if pr else None
        if ref is None:
            out["state"] = "NO_PR"
        else:
            rows, why = gh_pull_request_checks(
                ref["number"], ref["owner"], ref["repo"], ref["host"]
            )
            state, _ = checks_verdict(rows)
            out.update(state=state.upper(), checks=rows, reason=why)
    folder = phase_run_dir(root, int(phase))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(json.dumps(out, indent=2) + "\n")
    return out
