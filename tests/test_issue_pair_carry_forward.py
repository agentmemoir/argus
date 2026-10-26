# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A `--fresh` archive never takes the issue reservation pair with it (#287).

glide moves the whole run folder to `<folder>.prev-<epoch>`. A claim that wrote
the pair seconds before that move left the fresh folder without it, so the
ready sweep and the run's own post-steps refused the folder for good.
"""

import json
from pathlib import Path

import pytest

from argus.board import files
from argus.board.files import save_reservation_file
from argus.gitsync.git import ConfigError
from argus.gitsync.issue_paths import load_issue_reservation


def archive(runs: Path, epoch: int, reservation_id: str) -> Path:
    folder = runs / f"issue-000276.prev-{epoch}"
    save_reservation_file(
        folder / "reservation.json",
        {
            "issue": "276",
            "machine_id": "worker-a",
            "reservation_id": reservation_id,
            "generation": 1,
        },
    )
    (folder / "task-snapshot.json").write_text(
        json.dumps({"reservation_id": reservation_id}), encoding="utf-8"
    )
    return folder


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    (runs / "issue-000276").mkdir(parents=True)
    (runs / "issue-000276" / "run.json").write_text("{}", encoding="utf-8")

    def runs_dir(_root: object) -> Path:
        return runs

    monkeypatch.setattr(files, "runs_dir", runs_dir)
    return runs


def test_the_newest_archived_pair_is_copied_into_the_fresh_folder(runs: Path) -> None:
    archive(runs, 100, "rsv-old")
    newest = archive(runs, 200, "rsv-new")
    token = load_issue_reservation(runs.parent, 276)
    assert token["reservation_id"] == "rsv-new"
    fresh = runs / "issue-000276"
    for name in ("reservation.json", "task-snapshot.json"):
        assert (fresh / name).read_bytes() == (newest / name).read_bytes()
        assert (newest / name).is_file()


def test_a_folder_with_no_archived_pair_still_fails_closed(runs: Path) -> None:
    (runs / "issue-000276.prev-300").mkdir()
    with pytest.raises(ConfigError, match="does not belong to the current issue"):
        load_issue_reservation(runs.parent, 276)
    assert not (runs / "issue-000276" / "reservation.json").exists()


def test_a_folder_that_holds_its_own_pair_is_left_alone(runs: Path) -> None:
    own = archive(runs, 400, "rsv-own")
    for name in ("reservation.json", "task-snapshot.json"):
        (runs / "issue-000276" / name).write_bytes((own / name).read_bytes())
    archive(runs, 500, "rsv-later")
    assert load_issue_reservation(runs.parent, 276)["reservation_id"] == "rsv-own"
