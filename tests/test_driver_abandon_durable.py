# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A residue write that fails never loses the abandon.

The residue is staged before the board release, so a write that fails stops the
abandon with the claim still held and nothing released, and the same command
can run again. After the release only a rename is left, and a rename that fails
is reported as a failure, never as an abandon.

These cases drive the real ``abandon_cmd`` in the real shell harness. The stand
ins are the board process and the Python write the case makes fail.
"""

from pathlib import Path

from driver_maintenance_support import shell

BODY = """
. "$TEST_DRIVER/lib-driver-run.sh"
. "$TEST_DRIVER/lib-driver-halt-record.sh"
. "$TEST_DRIVER/lib-driver-abandon.sh"
python_transport() {
  case "$1" in
    */board/reservations.py) log RELEASE; return 0 ;;
    -) [ "$FAIL" = stage ] && return 1; "$TEST_PYTHON" "$@" ;;
    -c)
      case "$3:$FAIL" in *abandoned.json.pending:commit) return 1 ;; esac
      "$TEST_PYTHON" "$@" ;;
    *) "$TEST_PYTHON" "$@" ;;
  esac
}
resume_phase_file_of() { printf phase-100020-fixture.md; }
phase_run_dir() { printf '%s' "$RUN_DIR"; }
phase_state() { printf blocked; }
# A retry runs on the claim the first run left, never a fresh one.
[ -e "$CLAIM" ] || printf '100020\\trsv-fixture\\t2\\n' >"$CLAIM"
FAIL=WHICH
rc=0
(abandon_cmd 100020) || rc=$?
printf 'abandon_rc=%s\\n' "$rc"
"""


def _run(tmp_path: Path, which: str) -> tuple[str, list[str]]:
    output = shell(tmp_path, BODY.replace("WHICH", which))
    events_file = tmp_path / "events"
    events = events_file.read_text().splitlines() if events_file.is_file() else []
    return output, events


def test_a_failed_residue_write_releases_nothing_and_keeps_the_claim(
    tmp_path: Path,
) -> None:
    output, events = _run(tmp_path, "stage")

    assert "abandon_rc=1" in output
    assert "RELEASE" not in events, "the board was released before the residue"
    assert (tmp_path / "claim").read_text().strip() == "100020\trsv-fixture\t2"
    assert not (tmp_path / "abandoned.json").exists()


def test_a_retry_after_a_failed_residue_write_completes_the_abandon(
    tmp_path: Path,
) -> None:
    _run(tmp_path, "stage")
    output, events = _run(tmp_path, "none")

    assert "abandon_rc=0" in output
    assert events.count("RELEASE") == 1
    assert (tmp_path / "abandoned.json").is_file()
    assert not (tmp_path / "claim").exists()


def test_a_failed_rename_after_the_release_reports_failure(tmp_path: Path) -> None:
    output, events = _run(tmp_path, "commit")

    assert "abandon_rc=1" in output
    assert not any(line.startswith("abandoned") for line in events)
    assert not (tmp_path / "abandoned.json").exists()
    # The staged residue is kept: it is the record of the abandon.
    assert (tmp_path / "abandoned.json.pending").is_file()
