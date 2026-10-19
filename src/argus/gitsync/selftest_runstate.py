# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The run-state cleanup, clean-mode and checkpoint checks."""

import json
import subprocess
from pathlib import Path
from typing import Any

from argus.gitsync.git import dirty_paths, git, git_binary
from argus.gitsync.mode_clean import mode_clean
from argus.gitsync.runstate import commit_leftover_run_state
from argus.gitsync.selftest_fixture import BOARD_REL, _check, _init_remote


def _runstate_checks(base: Path, checks: list[dict[str, Any]]) -> None:
    # 10. a fresh start commits and pushes only the private repo's runs/
    # leftovers, then the private tree is clean.
    d = base / "runstate"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
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
    (nested / "runs").mkdir()
    (nested / "runs" / "ledger.json").write_text("{}\n")
    git(nested, "add", "runs/ledger.json")
    git(nested, "commit", "-q", "-m", "ledger")
    git(nested, "push", "-q", "origin", "main")
    (nested / "runs" / "ledger.json").write_text('{"finalize": true}\n')
    res: dict[str, Any] = commit_leftover_run_state(pub, "pipeline: test")
    _check(
        checks,
        "sync: start commits and pushes runs/ leftovers",
        res["ok"]
        and not dirty_paths(nested)
        and git(nested, "rev-parse", "HEAD")[1]
        == git(nested, "rev-parse", "origin/main")[1],
        res["summary"],
    )

    # 10b. a dirty file outside runs/ is reported by the start cleanup and the
    # phase-scoped commit keeps it out of the publish.
    d = base / "runstate-outside"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
    nested = pub / BOARD_REL
    nested.parent.mkdir(parents=True, exist_ok=True)
    priv_remote, _ = _init_remote(d, "priv")
    subprocess.run(
        [git_binary(), "clone", "-q", str(priv_remote), str(nested)],
        check=True,
        capture_output=True,
        text=True,
    )
    (nested / "priv.txt").write_text("human edit\n")
    res = commit_leftover_run_state(pub, "pipeline: test")
    _check(
        checks,
        "sync: start reports dirty files outside runs/ and continues",
        res["ok"]
        and any("outside runs/" in str(r.get("warning", "")) for r in res["repos"]),
        res["summary"],
    )

    # 11. Phase-scoped cleanup publishes only the requested run directory.
    d = base / "runstate-phase"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
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
    for number in (100440, 100601):
        path = nested / "runs" / f"phase-{number:06d}"
        path.mkdir(parents=True)
        (path / "run.json").write_text('{"state": "running"}\n')
    git(nested, "add", "runs")
    git(nested, "commit", "-q", "-m", "run dirs")
    git(nested, "push", "-q", "origin", "main")
    (nested / "runs" / "phase-100440" / "run.json").write_text(
        '{"state": "completed"}\n'
    )
    (nested / "runs" / "phase-100601" / "run.json").write_text('{"state": "blocked"}\n')
    res = commit_leftover_run_state(pub, "pipeline: phase test", phase=100440)
    _check(
        checks,
        "sync: cleanup is scoped to the current phase",
        res["ok"]
        and json.loads((nested / "runs" / "phase-100440" / "run.json").read_text())[
            "state"
        ]
        == "completed"
        and json.loads((nested / "runs" / "phase-100601" / "run.json").read_text())[
            "state"
        ]
        == "blocked"
        and "runs/phase-100601/run.json" in dirty_paths(nested),
        res["summary"],
    )

    # 11b. A finished phase's closing run state is committed and pushed by
    # --mode clean, and only that phase's state. This is the defect that halted
    # phase 100901: the runner writes run.json, ledger.json, resume.json's
    # deletion and the usage file after finalize's publish commit, the next
    # phase's publish saw them dirty and refused. It must work while the public
    # checkout is mid-work, which is why the check dirties it.
    d = base / "runstate-closeout"
    d.mkdir()
    _, pub = _init_remote(d, "pub")
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
    for number in (100440, 100601):
        path = nested / "runs" / f"phase-{number:06d}"
        path.mkdir(parents=True)
        (path / "run.json").write_text('{"state": "running"}\n')
        (path / "resume.json").write_text('{"current": "finalize"}\n')
    git(nested, "add", "runs")
    git(nested, "commit", "-q", "-m", "run dirs")
    git(nested, "push", "-q", "origin", "main")
    phase = nested / "runs" / "phase-100440"
    (phase / "run.json").write_text('{"state": "completed"}\n')
    (phase / "ledger.json").write_text('{"steps": {}}\n')
    (phase / "finalize-task-r1.log").write_text("log\nFINALIZE_DONE\n")
    (phase / "finalize-usage-r1.jsonl").write_text('{"ok": true}\n')
    (phase / "resume.json").unlink()
    # Another phase's partial record, which must be left where it is.
    (nested / "runs" / "phase-100601" / "run.json").write_text('{"state": "blocked"}\n')
    (pub / "uncommitted-public.txt").write_text("mid-work\n")
    res = mode_clean(pub, 100440)
    dirty_after: list[str] = dirty_paths(nested)
    _check(
        checks,
        "sync: clean commits a finished phase's closing run state",
        res["ok"]
        and not [p for p in dirty_after if p.startswith("runs/phase-100440/")]
        and "runs/phase-100601/run.json" in dirty_after
        and json.loads((phase / "run.json").read_text())["state"] == "completed",
        f"{res['summary']}; dirty={dirty_after}",
    )

    # 11c. With no --phase, --mode clean is the fresh-start sweep: every phase's
    # leftover run state is landed, each scoped to its own directory, so a phase
    # whose run ended without its own closing commit cannot block the next
    # phase's publish. The public checkout is mid-work and must stay untouched.
    res = mode_clean(pub)
    dirty_after_all: list[str] = dirty_paths(nested)
    _check(
        checks,
        "sync: clean with no phase lands every phase's leftover run state",
        res["ok"]
        and not [
            p
            for p in dirty_after_all
            if p.startswith("runs/phase-100440/") or p.startswith("runs/phase-100601/")
        ]
        and "uncommitted-public.txt" in dirty_paths(pub),
        f"{res['summary']}; dirty={dirty_after_all}",
    )


__all__ = ["_runstate_checks"]
