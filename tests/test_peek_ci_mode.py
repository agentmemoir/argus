# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The peek-ci mode: one read of a pull request's checks, never a wait."""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import conventions
from argus.gitsync import main
from argus.gitsync.git import ConfigError, git
from argus.gitsync.mode_peek_ci import mode_peek_ci

BRANCH = "feature/cache-warm"
URL = "https://github.com/example/argus/pull/11"

STUB = """#!__PYTHON__
import json
import sys
from pathlib import Path

transport = json.loads(Path(__TRANSPORT__).read_text())
call = sys.argv[1:]
with Path(__CALLS__).open("a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\\n")
if call[:2] == ["pr", "list"]:
    print(json.dumps(transport.get("list") or []))
elif call[:2] == ["pr", "checks"]:
    rows = transport.get("checks")
    if rows is not None:
        print(json.dumps(rows))
    raise SystemExit(int(transport.get("checks_exit") or 0))
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

    def answer(
        self,
        list: list[dict[str, object]] | None = None,
        checks: list[dict[str, object]] | None = None,
        checks_exit: int = 0,
        checks_reported: bool = True,
    ) -> None:
        """Set what the stub gh answers next.

        ``checks_reported=False`` emulates a branch with no checks at all: gh
        prints nothing, says so on stderr, and exits non-zero.
        """
        data: dict[str, object] = {
            "list": [] if list is None else list,
            "checks": checks if checks_reported else None,
            "checks_exit": checks_exit,
        }
        self.transport.write_text(json.dumps(data))

    def commands(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(row) for row in self.calls.read_text().splitlines()]

    def check_calls(self) -> list[list[str]]:
        return [call for call in self.commands() if call[:2] == ["pr", "checks"]]


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
    transport.write_text(json.dumps({"list": [], "checks": [], "checks_exit": 0}))
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
    return Workspace(root, phases, transport, calls)


def _row(name: str, bucket: str) -> dict[str, object]:
    return {
        "bucket": bucket,
        "state": bucket.upper(),
        "name": name,
        "link": f"https://example.test/{name}",
    }


def _open_pr(number: int = 11) -> list[dict[str, object]]:
    return [
        {
            "number": number,
            "state": "OPEN",
            "isDraft": True,
            "url": f"https://github.com/example/argus/pull/{number}",
        }
    ]


def test_every_check_passing_is_the_only_way_to_pass(workspace: Workspace) -> None:
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "pass"), _row("lint", "pass"), _row("test", "pass")],
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is True, out
    assert out["mode"] == "peek-ci"
    assert out["state"] == "passing"
    row = out["repos"][0]
    assert row["ok"] is True
    assert row["number"] == 11
    assert row["url"] == URL
    assert row["state"] == "passing"
    assert row["checks"] == {
        "pass": 3,
        "fail": 0,
        "pending": 0,
        "skipping": 0,
        "cancel": 0,
        "total": 3,
    }
    assert not any("--watch" in call for call in workspace.commands())


def test_one_failing_check_is_failing_and_not_passing(workspace: Workspace) -> None:
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "fail"), _row("lint", "pass")],
        checks_exit=1,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "failing"
    row = out["repos"][0]
    assert row["ok"] is False
    assert row["checks"]["fail"] == 1
    assert row["checks"]["pass"] == 1
    assert row["checks"]["total"] == 2
    assert any("build" in reason for reason in row["blockers"])
    assert "build" in out["summary"]


def test_a_pending_check_is_pending_and_the_mode_does_not_wait(
    workspace: Workspace,
) -> None:
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "pending"), _row("lint", "pass")],
        checks_exit=8,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "pending"
    assert out["repos"][0]["checks"]["pending"] == 1
    # One read, never a watch: gh was called once and no wait flag was passed.
    assert len(workspace.check_calls()) == 1
    assert not any("--watch" in call for call in workspace.commands())


def test_all_skipped_exits_zero_and_must_not_be_reported_as_passing(
    workspace: Workspace,
) -> None:
    # gh exits 0 when nothing failed and nothing is pending, which a skipped
    # check satisfies. Reading the exit code alone would call this a pass.
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "skipping"), _row("lint", "skipping")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["skipping"] == 2
    assert "build" in out["summary"]
    assert "lint" in out["summary"]
    assert any("skipped" in reason for reason in out["repos"][0]["blockers"])


def test_a_cancelled_check_is_not_a_pass(workspace: Workspace) -> None:
    workspace.answer(list=_open_pr(), checks=[_row("build", "cancel")], checks_exit=0)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert any("build" in reason for reason in out["repos"][0]["blockers"])


def test_an_unrecognised_bucket_is_named_in_the_summary(workspace: Workspace) -> None:
    # gh may add a bucket this mode does not know. Such a row is counted in the
    # total, so a summary that names only the four known buckets leaves it
    # unexplained while still saying the pull request did not pass.
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "pass"), _row("flaky-job", "neutral")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert "flaky-job" in out["summary"]
    assert "1 unknown bucket" in out["summary"]
    assert any("neutral" in reason for reason in out["repos"][0]["blockers"]), out[
        "repos"
    ][0]["blockers"]


def test_a_bucket_outside_the_known_set_is_not_a_pass(workspace: Workspace) -> None:
    # The whole dangerous path: every row is a non-pass, gh exits 0, and no
    # known bucket is non-zero. Reading only the known buckets calls this a pass.
    workspace.answer(
        list=_open_pr(),
        checks=[_row("flaky-job", "neutral")],
        checks_exit=0,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["checks"]["pass"] == 0
    assert row["checks"]["total"] == 1
    assert any("does not know" in reason for reason in row["blockers"])


def test_gh_exiting_zero_without_a_check_list_is_not_a_pass(
    workspace: Workspace,
) -> None:
    # gh answered 0 but printed no list at all, so there are no rows to read.
    # A code of 0 with no rows is a fault, not an empty passing list.
    workspace.answer(list=_open_pr(), checks_exit=0, checks_reported=False)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    blockers = out["repos"][0]["blockers"]
    assert any("without a check list" in reason for reason in blockers), blockers


def test_a_non_zero_exit_beside_all_pass_rows_is_not_a_pass(
    workspace: Workspace,
) -> None:
    # The rows and the exit code disagree. Trusting the rows alone would report
    # a pass on an exit code gh uses to say something went wrong.
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "pass")],
        checks_exit=8,
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["ok"] is False
    assert row["checks"]["pass"] == 1
    assert any(
        "gh exited 8 while every row said pass" in reason for reason in row["blockers"]
    ), row["blockers"]


def test_an_empty_check_list_is_not_a_pass(workspace: Workspace) -> None:
    workspace.answer(list=_open_pr(), checks=[], checks_exit=0)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    assert any("no check rows" in reason for reason in out["repos"][0]["blockers"])


def test_no_checks_reported_at_all_is_not_a_pass(workspace: Workspace) -> None:
    # What gh really does on a branch with no checks: nothing on stdout, a
    # message on stderr, and a non-zero exit.
    workspace.answer(list=_open_pr(), checks_exit=1, checks_reported=False)

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    assert out["repos"][0]["checks"]["total"] == 0
    blockers = out["repos"][0]["blockers"]
    assert any("could not report checks" in reason for reason in blockers)


def test_no_pull_request_for_the_branch_is_a_reason_not_a_crash(
    workspace: Workspace,
) -> None:
    workspace.answer(list=[])

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["state"] == "unknown"
    row = out["repos"][0]
    assert row["number"] is None
    assert any(BRANCH in reason for reason in row["blockers"])
    assert workspace.check_calls() == []


def test_a_closed_pull_request_has_no_checks_in_flight(workspace: Workspace) -> None:
    workspace.answer(
        list=[
            {
                "number": 9,
                "state": "MERGED",
                "isDraft": False,
                "url": "https://github.com/example/argus/pull/9",
            }
        ]
    )

    out = mode_peek_ci(workspace.root, branch=BRANCH)

    assert out["ok"] is False, out
    assert out["repos"][0]["number"] == 9
    assert any("MERGED" in reason for reason in out["repos"][0]["blockers"])
    assert workspace.check_calls() == []


def test_an_explicit_pull_request_still_reports_the_branch(
    workspace: Workspace,
) -> None:
    # The pull request comes from --pull-request, but the branch is still a fact
    # the reader needs, so it is resolved and reported rather than left null.
    workspace.answer(checks=[_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    assert out["branch"] == "main"


def test_an_explicit_pull_request_survives_a_branch_that_cannot_be_resolved(
    workspace: Workspace,
) -> None:
    # A URL names the pull request on its own, so a checkout on a detached HEAD
    # is not a reason to refuse the read: the branch is null and the read holds.
    assert git(workspace.root, "checkout", "-q", "--detach")[0] == 0
    workspace.answer(checks=[_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    assert out["branch"] is None


def test_an_explicit_url_is_read_directly(workspace: Workspace) -> None:
    workspace.answer(checks=[_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request=URL)

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["number"] == 11
    assert row["url"] == URL
    assert [call for call in workspace.commands() if call[:2] == ["pr", "list"]] == []
    checks = workspace.check_calls()
    assert len(checks) == 1
    assert URL in checks[0]
    assert checks[0][checks[0].index("--json") + 1] == "bucket,state,name,link"


def test_a_url_without_a_number_is_named_by_the_url(workspace: Workspace) -> None:
    # A URL ending in /files carries no pull request number. The blocker names
    # the URL instead of reading "for #None".
    files = f"{URL}/files"
    workspace.answer(checks_exit=1, checks_reported=False)

    out = mode_peek_ci(workspace.root, pull_request=files)

    assert out["ok"] is False, out
    assert out["repos"][0]["number"] is None
    blockers = out["repos"][0]["blockers"]
    assert any(files in reason for reason in blockers), blockers
    assert not any("#None" in reason for reason in blockers), blockers


def test_an_explicit_number_keeps_the_url_unknown(workspace: Workspace) -> None:
    workspace.answer(checks=[_row("build", "pass")])

    out = mode_peek_ci(workspace.root, pull_request="42")

    assert out["ok"] is True, out
    row = out["repos"][0]
    assert row["number"] == 42
    assert row["url"] is None
    assert "42" in workspace.check_calls()[0]


def test_a_value_that_is_not_a_pull_request_is_refused_before_gh(
    workspace: Workspace,
) -> None:
    with pytest.raises(ConfigError) as refused:
        mode_peek_ci(workspace.root, pull_request="not-a-pull-request")
    assert "not a pull request number or URL" in str(refused.value)
    assert workspace.commands() == []


def test_the_cli_accepts_the_peek_ci_mode(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    workspace.answer(list=_open_pr(), checks=[_row("build", "pass")])

    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "peek-ci", "--branch", BRANCH]
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "peek-ci"
    assert out["ok"] is True
    assert out["repos"][0]["state"] == "passing"


def test_a_pending_pull_request_exits_one(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    workspace.answer(
        list=_open_pr(),
        checks=[_row("build", "pending")],
        checks_exit=8,
    )

    monkeypatch.setattr(
        sys, "argv", ["gitsync", "--mode", "peek-ci", "--branch", BRANCH]
    )
    assert main.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "pending"


def test_the_repo_flag_names_the_checkout_to_peek_at(
    workspace: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # peek-ci has its own dispatch branch, which comes before the generic --repo
    # branch. Without handling it there, --repo fell through to the repo root and
    # read the wrong checkout.
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setattr(conventions, "configure_from_args", _keep_settings)
    workspace.answer(list=_open_pr(), checks=[_row("build", "pass")])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gitsync",
            "--mode",
            "peek-ci",
            "--repo",
            str(other),
            "--branch",
            BRANCH,
        ],
    )
    assert main.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repos"][0]["repo"] == str(other.resolve())
