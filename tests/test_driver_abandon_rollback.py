# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Abandon releases the board before it parks the phase, or parks nothing.

One state either way: the board group is released and the residue is written,
or the claim is still the machine's and no residue exists. The release is the
step that can fail, so it runs first; a failure stops before the residue and
there is no compensating write to fail on top of it.

These cases drive the real ``abandon_cmd`` in the real shell harness. The only
stand-in is the board process on the far side of the ``release-group`` argv,
which is what a fixture board would have to be anyway.
"""

import json
from pathlib import Path

import pytest

from driver_maintenance_support import shell

RELEASE_BODY = """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
# The fence itself holds: this case is about the release that follows it.
board_assert_owner() { log ASSERT; return 0; }
# The board process: the only stand-in, and it answers by exit code exactly as
# the real reservation command does.
python_transport() {
  case "$1" in
    */board/reservations.py) log RELEASE; return RELEASE_RC ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
rc=0
(abandon_cmd 100020) || rc=$?
printf 'abandon_rc=%s\\n' "$rc"
"""


def _run(tmp_path: Path, release_rc: int) -> tuple[str, list[str]]:
    output = shell(tmp_path, RELEASE_BODY.replace("RELEASE_RC", str(release_rc)))
    events = []
    if (tmp_path / "events").is_file():
        events = (tmp_path / "events").read_text().splitlines()
    return output, events


def test_a_failed_release_parks_nothing_and_keeps_the_claim(tmp_path: Path) -> None:
    output, events = _run(tmp_path, 1)

    assert "abandon_rc=1" in output
    assert events == ["ASSERT", "RELEASE"]
    assert not (tmp_path / "abandoned.json").exists(), "a failed release parked it"
    assert (tmp_path / "claim").is_file(), "a failed release dropped the claim"
    assert (tmp_path / "claim").read_text().strip() == "100020\trsv-fixture\t2"


def test_a_successful_release_writes_the_residue_after_it(tmp_path: Path) -> None:
    output, events = _run(tmp_path, 0)

    assert "abandon_rc=0" in output
    assert events[:2] == ["ASSERT", "RELEASE"]
    assert any("abandoned" in line for line in events[2:])
    residue = json.loads((tmp_path / "abandoned.json").read_text())
    assert residue["phase"] == "100020"
    assert residue["reservation_id"] == "rsv-fixture"
    assert not (tmp_path / "claim").exists(), "a completed abandon kept the claim"


def test_the_release_argv_names_the_fence_it_releases(tmp_path: Path) -> None:
    # The ordering only means "released, then parked" if the release names the
    # same reservation the fence proved. Recorded by the board stand-in itself.
    shell(
        tmp_path,
        """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
board_assert_owner() { return 0; }
python_transport() {
  case "$1" in
    */board/reservations.py) printf '%s\\n' "$*" >>"$RUN_DIR/release-argv" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
(abandon_cmd 100020) || true
""",
    )

    argv = (tmp_path / "release-argv").read_text()
    assert "release-group" in argv
    assert "--reservation-id rsv-fixture" in argv
    assert "--generation 2" in argv
    assert "--repo-root" in argv
    assert (tmp_path / "abandoned.json").is_file()


@pytest.mark.parametrize("release_rc", [0, 1])
def test_the_ordering_holds_for_both_answers(tmp_path: Path, release_rc: int) -> None:
    # Parked or not, the two states are the only ones: a residue exists if and
    # only if the claim was released.
    _output, _events = _run(tmp_path, release_rc)
    parked = (tmp_path / "abandoned.json").exists()
    claimed = (tmp_path / "claim").exists()
    assert parked is not claimed
