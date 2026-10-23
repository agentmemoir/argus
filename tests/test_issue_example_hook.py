# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The example issue pipeline's post_step hook exists and gates its step."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "examples" / "project" / "issue-pipeline.yaml"


def _load_hook() -> ModuleType:
    path = REPO / "examples" / "project" / "scripts" / "post_step.py"
    spec = importlib.util.spec_from_file_location("example_post_step", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_gitsync(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[tuple[int, dict[str, Any]]],
    base_sync: tuple[int, str] = (0, "base-sync: no conflict with master"),
) -> list[list[str]]:
    hook = _load_hook()
    calls: list[list[str]] = []
    gitsync_calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, "issue/248-fixture\n", "")
        if "argus.gitsync.base_sync_cli" in argv:
            returncode, text = base_sync
            return subprocess.CompletedProcess(argv, returncode, text, "")
        gitsync_calls.append(argv)
        returncode, payload = outcomes[len(gitsync_calls) - 1]
        return subprocess.CompletedProcess(argv, returncode, json.dumps(payload), "")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    monkeypatch.setenv("GLIDE_RUN_DIR", "/run")
    return calls


def test_example_hook_points_at_a_script_that_exists() -> None:
    hook = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["hooks"]["post_step"]
    assert hook[0] == "python3", hook
    assert (REPO / hook[1]).is_file(), hook


@pytest.mark.parametrize(
    "refusal",
    [
        (2, {"ok": False, "state": "CONFIG_ERROR", "error": "refused"}),
        (0, {"ok": True, "warning": "could not stage issue work"}),
    ],
)
def test_example_hook_exits_non_zero_when_checkpoint_refuses(
    monkeypatch: pytest.MonkeyPatch, refusal: tuple[int, dict[str, Any]]
) -> None:
    calls = _fake_gitsync(monkeypatch, [refusal])
    assert _load_hook().main() == 1
    assert len(calls) == 1, "open-pr must not run after a refused checkpoint"


def test_example_hook_passes_the_body_file_and_exits_zero_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = (0, {"ok": True, "state": "OPEN"})
    calls = _fake_gitsync(monkeypatch, [ok, ok])
    assert _load_hook().main() == 0
    open_pr = [call for call in calls if "open-pr" in call]
    assert open_pr[0][-2:] == ["--body-file", "/run/pr-body.md"], calls


def test_base_sync_runs_before_open_pr_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = (0, {"ok": True, "state": "OPEN"})
    calls = _fake_gitsync(monkeypatch, [ok, ok])
    assert _load_hook().main() == 0
    base = [i for i, call in enumerate(calls) if "argus.gitsync.base_sync_cli" in call]
    pr = [i for i, call in enumerate(calls) if "open-pr" in call]
    assert base and pr and base[0] < pr[0], calls
    assert calls[base[0]][-4:] == ["--repo", ".", "--pr-branch", "issue/248-fixture"]


@pytest.mark.parametrize(
    "outcome",
    [
        (3, "base-sync: text conflict with origin/master; resolve, then commit"),
        (1, "base-sync: failed: git fetch origin failed"),
    ],
)
def test_base_sync_stop_exits_non_zero_before_open_pr(
    monkeypatch: pytest.MonkeyPatch, outcome: tuple[int, str]
) -> None:
    ok = (0, {"ok": True, "state": "OPEN"})
    calls = _fake_gitsync(monkeypatch, [ok, ok], base_sync=outcome)
    assert _load_hook().main() == 1
    assert not [call for call in calls if "open-pr" in call], calls
