# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The per-tick sweep that readies a group's shared pull request.

The boundary is a real ``gh`` executable on ``PATH`` that answers from a JSON
plan and records every argv it was given, plus the real phase run directory the
publish step writes. Nothing is monkeypatched between the mode and the process
it starts, so the test can only pass by the mode calling the command a person
would run by hand. The shim also *acts*: a ``pr ready`` call appends the ready
event GitHub would record, so the next tick sees the history it would see.

Every phase receipt here reads ``published``, which is the real lifecycle: a
phase leaves the driver's pending list the tick its receipt settles, so a retry
that lived inside ``--mode published`` ran once and never again.
"""

import atexit
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from argus import conventions
from argus.exec_tempdir import exec_tempdir
from argus.gitsync.main import main
from argus.gitsync.modes import mode_ready
from argus.gitsync.selftest_publish import _publish_fixture
from gh_mode_support import keep_settings

PHASE = 100501
SIBLING = 100500
PULL_REQUEST = "https://github.com/example-org/example-repo/pull/7"
READY = ["pr", "ready", "7", "--repo", "example-org/example-repo"]


@pytest.fixture(autouse=True)
def git_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commands the publish fixture needs before it configures the rest."""
    monkeypatch.setattr(
        conventions, "_settings", {"commands.git": "git", "commands.gh": "gh"}
    )


# The shim is a real program, so the code under test starts it the way it starts
# gh: as an argument list, through PATH. It reads one plan file per answer and
# appends every argv to argv.jsonl, which is the observable the test asserts on.
# A ready call rewrites the plan the way GitHub would, so a later tick in the
# same run sees a ready event in the pull request's history.
SHIM = """#!{python}
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_GH_STATE"])
args = sys.argv[1:]
with (state / "argv.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
path = state / "gh-state.json"
plan = json.loads(path.read_text())
if args[:2] == ["api", "graphql"]:
    page = {{"data": {{"repository": {{"pullRequest": plan["pull_request"]}}}}}}
    print(json.dumps([page]))
    sys.exit(0)
if args[:2] == ["pr", "list"]:
    open_now = plan["pull_request"]["state"] == "OPEN"
    is_draft = plan["pull_request"]["isDraft"]
    print(json.dumps([{{"number": 7, "isDraft": is_draft}}] if open_now else []))
    sys.exit(0)
if args[:2] == ["pr", "checks"]:
    print(json.dumps(plan["checks"]["rows"]))
    sys.exit(plan["checks"]["exit"])
if args[:2] == ["pr", "ready"]:
    pull = plan["pull_request"]
    pull["isDraft"] = False
    pull["timelineItems"]["nodes"].append(
        {{"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-02T00:00Z"}}
    )
    path.write_text(json.dumps(plan))
    sys.exit(0)
sys.exit(1)
"""


def _github(state: Path, **plan: object) -> None:
    """Write the plan the fake gh answers the next tick from."""
    base: dict[str, object] = {
        "pull_request": {
            "state": "OPEN",
            "isDraft": True,
            "timelineItems": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        },
        "checks": {"rows": [], "exit": 0},
    }
    base.update(plan)
    (state / "gh-state.json").write_text(json.dumps(base), encoding="utf-8")


def _ready_calls(state: Path) -> list[list[str]]:
    lines = (state / "argv.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        json.loads(line) for line in lines if json.loads(line)[:2] == ["pr", "ready"]
    ]


def _checks(rows: list[dict[str, str]], code: int) -> dict[str, object]:
    return {"checks": {"rows": rows, "exit": code}}


PENDING = [{"bucket": "pending", "state": "PENDING", "name": "verify", "link": ""}]
GREEN = [{"bucket": "pass", "state": "SUCCESS", "name": "verify", "link": ""}]
PRIOR_READY = [{"__typename": "ReadyForReviewEvent", "createdAt": "2026-01-01T00:00Z"}]
REDRAFTED = PRIOR_READY + [
    {"__typename": "ConvertToDraftEvent", "createdAt": "2026-01-02T00:00Z"}
]


def _receipt(phase: int, state: str) -> dict[str, object]:
    """One member's publication receipt, as publish leaves it when it settles."""
    return {
        "phase": f"{phase:06d}",
        "state": state,
        "branch": "feature/group",
        "public_head": "a" * 40,
        "private_head": "b" * 40,
        "pull_request": PULL_REQUEST,
        "group": [str(SIBLING), str(PHASE)],
    }


def _group_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    """A published group member in the real run directory the publish step wrote.

    Both members' receipts read ``published``. That is the state the driver
    leaves them in once a human merges, and it is the state a retry has to keep
    working in: a ``published`` receipt is not on the pending list, so nothing
    runs ``--mode published`` for it again.
    """
    pub, _nested, run_dir = _publish_fixture(tmp_path, "ready-tick", PHASE)
    (run_dir / "publication.json").write_text(
        json.dumps(_receipt(PHASE, "published")), encoding="utf-8"
    )
    sibling = run_dir.parent / f"phase-{SIBLING:06d}"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "publication.json").write_text(
        json.dumps(_receipt(SIBLING, "published")), encoding="utf-8"
    )
    state = tmp_path / "gh"
    state.mkdir()
    # A shim in the default temporary directory is not enough: a host that
    # mounts it noexec makes execvp skip the shim and run the host's gh, which
    # answers "not authenticated" and would measure the machine, not the code.
    executable = exec_tempdir(purpose="the group ready tick test's fake gh")
    atexit.register(executable.cleanup)
    binary = Path(executable.name) / "gh"
    binary.write_text(SHIM.format(python=sys.executable))
    binary.chmod(0o755)
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    assert shutil.which("gh") == str(binary), "the host's gh answered, not the shim"
    _github(state, **_checks(PENDING, 8))
    return pub, run_dir, state


def test_a_pending_check_report_leaves_the_pull_request_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)

    result = mode_ready(pub)

    assert result["state"] == "HELD", result
    assert _ready_calls(state) == [], "a pull request with running checks was readied"
    assert "not all green" in result["results"][0]["detail"], result["results"]


def test_green_checks_ready_the_pull_request_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    mode_ready(pub)
    assert _ready_calls(state) == []

    _github(state, **_checks(GREEN, 0))
    result = mode_ready(pub)

    assert _ready_calls(state) == [READY], "the green checks did not ready it once"
    assert result["state"] == "READY", result
    assert result["ok"] is True, result


def _graphql_calls(state: Path) -> int:
    lines = (state / "argv.jsonl").read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if json.loads(line)[:2] == ["api", "graphql"])


def test_a_later_tick_reads_no_timeline_once_github_lists_it_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The readied pull request is not a draft, so the sweep never reads it."""
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    _github(state, **_checks(GREEN, 0))
    mode_ready(pub)
    assert _ready_calls(state) == [READY]
    reads_before = _graphql_calls(state)

    third = mode_ready(pub)

    assert _ready_calls(state) == [READY], "a readied pull request was readied again"
    assert _graphql_calls(state) == reads_before, "the timeline was read again"
    assert third["results"] == [], third["results"]


def test_a_check_report_that_finishes_three_ticks_later_readies_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case a retry inside ``--mode published`` missed.

    The receipt is already ``published``, so the driver's pending list is empty
    and nothing would ever run ``--mode published`` again. Only a sweep that runs
    on every tick reaches the pull request on the tick its checks finally pass.
    """
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    for _tick in range(3):
        mode_ready(pub)
        assert _ready_calls(state) == [], "a pending check readied the pull request"

    _github(state, **_checks(GREEN, 0))
    fourth = mode_ready(pub)

    assert _ready_calls(state) == [READY], "the late green checks did not ready it"
    assert fourth["state"] == "READY", fourth


def test_a_human_redraft_is_never_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    # A ready event followed by a draft change: a person put it back in draft,
    # and the checks are green again. Nothing may make it ready a second time.
    _github(
        state,
        pull_request={
            "state": "OPEN",
            "isDraft": True,
            "timelineItems": {
                "nodes": REDRAFTED,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        },
        **_checks(GREEN, 0),
    )

    result = mode_ready(pub)

    assert _ready_calls(state) == [], "a person's draft change was undone"
    assert any(r.get("skipped") is True for r in result["results"]), result["results"]


def test_the_cli_runs_the_sweep_through_the_real_argument_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--mode ready`` must be reachable as a command, not only as a function."""
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    _github(state, **_checks(GREEN, 0))
    monkeypatch.setattr(sys, "argv", ["argus", "--root", str(pub), "--mode", "ready"])
    # This case is about the parser and the dispatch, not the config loader: the
    # fixture configured this process in memory and there is no file to read.
    monkeypatch.setattr(conventions, "configure_from_args", keep_settings)

    code = main()

    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["mode"] == "ready" and out["state"] == "READY", out
    assert _ready_calls(state) == [READY], "the command did not ready the pull request"


def _calls(state: Path, head: list[str]) -> list[list[str]]:
    lines = (state / "argv.jsonl").read_text(encoding="utf-8").splitlines()
    calls = [json.loads(line) for line in lines]
    return [call for call in calls if call[: len(head)] == head]


def test_a_group_reads_its_pull_request_once_per_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both members carry one pull request, so one sweep reads it once.

    Before the per-sweep memo, each member's receipt read the timeline and the
    checks again. The second read is the cost this test names.
    """
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)

    result = mode_ready(pub)

    assert len(_calls(state, ["api", "graphql"])) == 1, (
        "the timeline was read per member"
    )
    assert len(_calls(state, ["pr", "checks"])) == 1, "the checks were read per member"
    assert result["state"] == "HELD", result


def test_a_green_group_is_readied_once_with_one_timeline_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, _run_dir, state = _group_fixture(tmp_path, monkeypatch)
    _github(state, **_checks(GREEN, 0))

    result = mode_ready(pub)

    assert _ready_calls(state) == [READY], "the green group was not readied once"
    assert len(_calls(state, ["api", "graphql"])) == 1, (
        "the timeline was read per member"
    )
    assert [r["state"] for r in result["results"]] == ["READY", "READY"], result
    assert any(r.get("skipped") is True for r in result["results"]), result["results"]
