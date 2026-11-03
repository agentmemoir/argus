# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One stale task snapshot never stops the issue leftover sweep (#313).

A run folder whose frozen task file no longer matches its snapshot made the
sweep raise a configuration error, so no other issue's leftovers landed. The
sweep now skips that folder, names it and the reason, and lands the others.
"""

from pathlib import Path

from argus.board.files import reservation_file
from argus.gitsync.git import git
from argus.gitsync.issue_runstate import mode_issue_clean
from argus.gitsync.modes import result_exit_code
from test_issue_publication_fixture import Publication
from test_issue_publication_fixture import (
    publication_fixture as publication_fixture,  # noqa: F401
)

STALE = 1234567
GOOD = 1234568


def test_a_stale_snapshot_is_skipped_and_the_good_folder_lands(
    publication: Publication,
) -> None:
    _token, stale_task = publication.claim(STALE)
    publication.claim(GOOD)
    runs = reservation_file(publication.root, GOOD, kind="issue").parent.parent
    stale_folder = runs / f"issue-{STALE:06d}"
    good_folder = runs / f"issue-{GOOD:06d}"
    (stale_folder / "leftover.json").write_text('{"rc": 2}\n', encoding="utf-8")
    (good_folder / "leftover.json").write_text('{"rc": 2}\n', encoding="utf-8")
    # The frozen task file changes after its snapshot was written.
    stale_task.write_text("edited after the claim\n", encoding="utf-8")

    result = mode_issue_clean(publication.root)

    assert result["ok"], result
    assert result_exit_code(result) == 0
    assert [repo["issue"] for repo in result["repos"]] == [GOOD]
    assert result["state"] == "PARTIAL"
    [skip] = result["skipped"]
    assert skip["folder"] == stale_folder.name
    assert "no valid frozen task snapshot" in skip["error"]
    _assert_landed(publication.private, good_folder)
    _assert_left_alone(publication.private, stale_folder, stale_task)


def _assert_landed(private: Path, folder: Path) -> None:
    for path in (folder / "leftover.json", folder / "reservation.json"):
        rel = path.relative_to(private).as_posix()
        assert git(private, "ls-files", "--", rel)[1] == rel
    task = next((private / "tasks").glob(f"issue-{GOOD:06d}-*.md"))
    rel = task.relative_to(private).as_posix()
    assert git(private, "ls-files", "--", rel)[1] == rel


def _assert_left_alone(private: Path, folder: Path, task: Path) -> None:
    assert (folder / "leftover.json").read_text(encoding="utf-8") == '{"rc": 2}\n'
    assert (folder / "reservation.json").is_file()
    assert task.read_text(encoding="utf-8") == "edited after the claim\n"
    rel = folder.relative_to(private).as_posix()
    assert git(private, "ls-files", "--", rel)[1] == ""
