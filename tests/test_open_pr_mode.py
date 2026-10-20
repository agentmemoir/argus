# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The open-pr mode: one draft for a branch, reused rather than duplicated."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import main
from argus.gitsync.git import ConfigError, git
from argus.gitsync.mode_open_pr import mode_open_pr

BRANCH = "feature/cache-warm"
URL = "https://github.com/example/argus/pull/11"
# What the read after a create reports for the new pull request.
CREATED = {"number": 11, "state": "OPEN", "isDraft": True, "url": URL}

STUB = """#!__PYTHON__
import json
import sys
from pathlib import Path

transport = json.loads(Path(__TRANSPORT__).read_text())
call = sys.argv[1:]
log = Path(__CALLS__)
seen = log.read_text().splitlines() if log.exists() else []
reads = sum(1 for item in seen if json.loads(item)[:2] == ["pr", "list"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(call) + "\\n")
if call[:2] == ["pr", "list"]:
    later = transport.get("reread")
    print(json.dumps(later if reads and later is not None else (transport.get("list") or [])))
    raise SystemExit(int(transport.get("list_exit", 0)))
elif call[:2] == ["pr", "create"]:
    print(transport.get("create") or "")
    raise SystemExit(int(transport.get("create_exit", 0)))
else:
    raise SystemExit(3)
"""


@dataclass
class Workspace:
    """A checkout, the phases folder beside it, and a stubbed gh."""

    root: Path
    phases: Path
    transport: Path
    calls: Path

    def answer(self, **data: object) -> None:
        """Set what the stub gh prints next."""
        self.transport.write_text(json.dumps(data))

    def commands(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(row) for row in self.calls.read_text().splitlines()]


def _keep_settings(_args: object) -> None:
    return None


@pytest.fixture
def workspace(
    tmp_path: Path, exec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Workspace:
    root = tmp_path / "public"
    root.mkdir()
    phases = root / "phases"
    phases.mkdir()
    # The stub lives where the host really runs a file; only its data is temp.
    stub = exec_dir / "gh"
    transport = tmp_path / "transport.json"
    calls = tmp_path / "calls.jsonl"
    transport.write_text(json.dumps({"list": [], "create": URL, "reread": [CREATED]}))
    stub.write_text(
        STUB.replace("__PYTHON__", sys.executable)
        .replace("__TRANSPORT__", repr(str(transport)))
        .replace("__CALLS__", repr(str(calls)))
    )
    stub.chmod(0o755)
    monkeypatch.setattr(
        conventions,
        "_settings",
        {
            "commands.git": "git",
            "commands.gh": str(stub),
            "repo-root": str(root),
            "phases.directory": str(phases),
            "driver.include": ["phase-*.md"],
            "driver.exclude": [],
        },
    )
    assert git(root, "init", "-q", "-b", "main")[0] == 0
    (root / "product.txt").write_text("work\n")
    assert git(root, "add", "-A")[0] == 0
    assert (
        git(
            root,
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-qm",
            "work",
        )[0]
        == 0
    )
    # An explicit --branch must be a branch this checkout has, so the fixture
    # makes the one the tests name.
    assert git(root, "branch", BRANCH)[0] == 0
    return Workspace(root, phases, transport, calls)


def test_creates_a_draft_and_reports_what_the_read_back_says(
    workspace: Workspace,
) -> None:
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    assert out["mode"] == "open-pr"
    row = out["repos"][0]
    assert row["action"] == "created"
    # These four come from the read after the create, not from the create.
    assert row["number"] == 11
    assert row["url"] == URL
    assert row["draft"] is True
    assert row["state"] == "OPEN"
    creates = [call for call in workspace.commands() if call[:2] == ["pr", "create"]]
    assert len(creates) == 1
    create = creates[0]
    assert "--draft" in create
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--title") + 1] == BRANCH
    reads = [call for call in workspace.commands() if call[:2] == ["pr", "list"]]
    assert len(reads) == 2, reads


def test_a_create_that_prints_no_url_is_blocked(workspace: Workspace) -> None:
    workspace.answer(list=[], create="")

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "blocked"
    row = out["repos"][0]
    assert row["action"] is None
    assert row["number"] is None
    assert row["url"] is None
    assert row["blockers"]
    assert any("no pull request URL" in reason for reason in row["blockers"])


def test_a_create_that_cannot_be_read_back_is_blocked(workspace: Workspace) -> None:
    workspace.answer(list=[], create=URL, reread=[])

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "blocked"
    row = out["repos"][0]
    assert row["number"] is None
    assert any(URL in reason for reason in row["blockers"])


def test_a_failed_read_is_blocked_and_creates_nothing(workspace: Workspace) -> None:
    workspace.answer(list_exit=1)

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "blocked"
    row = out["repos"][0]
    assert any("could not list pull requests" in reason for reason in row["blockers"])
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_a_failed_create_is_blocked_and_claims_nothing(workspace: Workspace) -> None:
    workspace.answer(list=[], create=URL, create_exit=1)

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "blocked"
    row = out["repos"][0]
    assert row["action"] is None
    assert row["number"] is None
    assert row["url"] is None
    assert any("could not create" in reason for reason in row["blockers"])


def test_reuses_the_open_pull_request_and_creates_nothing(
    workspace: Workspace,
) -> None:
    workspace.answer(
        list=[
            {"number": 7, "state": "OPEN", "isDraft": True, "url": "u/7"},
            {"number": 5, "state": "OPEN", "isDraft": False, "url": "u/5"},
        ]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["action"] == "reused"
    assert row["number"] == 7
    assert row["url"] == "u/7"
    assert row["draft"] is True
    assert row["state"] == "OPEN"
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_closed_or_merged_pull_request_blocks_a_duplicate(
    workspace: Workspace, state: str
) -> None:
    workspace.answer(
        list=[{"number": 9, "state": state, "isDraft": False, "url": "u/9"}]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "blocked"
    row = out["repos"][0]
    assert row["number"] == 9
    assert row["state"] == state
    assert any("#9" in reason and state in reason for reason in row["blockers"])
    assert [call for call in workspace.commands() if call[:2] == ["pr", "create"]] == []


def test_a_phase_with_no_phases_file_is_refused(workspace: Workspace) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, phase=123456)
    assert "no phases file" in str(refused.value)
    assert workspace.commands() == []


def test_the_phase_file_names_the_branch(workspace: Workspace) -> None:
    (workspace.phases / "phase-100440-backup-archive-format.md").write_text("# x\n")
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, phase=100440)

    assert out["branch"] == "feature/backup-archive-format"
    assert out["ok"] is True, out


def test_an_explicit_branch_beats_the_phase_branch(workspace: Workspace) -> None:
    (workspace.phases / "phase-100440-backup-archive-format.md").write_text("# x\n")
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root, phase=100440, branch=BRANCH)

    assert out["branch"] == BRANCH


def test_the_current_branch_is_used_when_neither_flag_is_given(
    workspace: Workspace,
) -> None:
    assert git(workspace.root, "checkout", "-q", BRANCH)[0] == 0
    workspace.answer(list=[], create=URL, reread=[CREATED])

    out = mode_open_pr(workspace.root)

    assert out["branch"] == BRANCH
    assert out["ok"] is True, out


def test_a_detached_head_is_refused_by_name(workspace: Workspace) -> None:
    assert git(workspace.root, "checkout", "-q", "--detach", "HEAD")[0] == 0

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root)

    assert "detached HEAD" in str(refused.value)
    assert "has no commit yet" not in str(refused.value)
    assert workspace.commands() == []


@pytest.mark.usefixtures("workspace")
def test_a_checkout_with_no_commit_is_refused_by_name(tmp_path: Path) -> None:
    # The workspace fixture loads the git setting; the checkout under test is a
    # second, empty one.
    root = tmp_path / "fresh"
    root.mkdir()
    assert git(root, "init", "-q", "-b", "main")[0] == 0

    with pytest.raises(ConfigError) as refused:
        mode_open_pr(root)

    assert "has no commit yet" in str(refused.value)
    assert "detached HEAD" not in str(refused.value)


def test_a_branch_this_checkout_lacks_is_refused_before_gh(
    workspace: Workspace,
) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_open_pr(workspace.root, branch="feature/typo")

    assert "feature/typo" in str(refused.value)
    assert workspace.commands() == []


def test_a_pull_request_row_with_a_blank_url_is_not_reported(
    workspace: Workspace,
) -> None:
    workspace.answer(
        list=[
            {"number": 8, "state": "OPEN", "isDraft": True, "url": ""},
            {"number": 6, "state": "OPEN", "isDraft": False, "url": "u/6"},
        ]
    )

    out = mode_open_pr(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["action"] == "reused"
    assert row["number"] == 6
    assert row["url"] == "u/6"


def test_the_cli_accepts_open_pr_and_reports_config_error(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    workspace.answer(list=[], create=URL, reread=[CREATED])

    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--branch", BRANCH]
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "open-pr"
    assert out["repos"][0]["action"] == "created"

    workspace.calls.unlink()
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--phase", "123456"]
    )
    assert main.main() == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["state"] == "config_error"
    assert "no phases file" in refused["error"]
    assert workspace.commands() == []


def test_a_create_the_cli_cannot_confirm_exits_one(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    workspace.answer(list=[], create="")
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--branch", BRANCH]
    )

    assert main.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["state"] == "blocked"


def test_blocked_exits_one(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.answer(
        list=[{"number": 9, "state": "MERGED", "isDraft": False, "url": "u/9"}]
    )
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "open-pr", "--branch", BRANCH]
    )

    assert main.main() == 1
