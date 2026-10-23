# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The tick records the reservation file its own fenced steps read.

Every write path fenced by issue 164 reads ``runs/<phase>/reservation.json`` and
asks the board whether the machine, reservation id, and generation in it still
own the phase. The driver's own claim file is a shape only the driver shell
reads, so a tick that recorded just that one held the phase and proved nothing
to the steps it was about to call: its tidy, branch, clean, and checkpoint steps
refused it, and the refusal named a fence the tick believed it had.

These cases pin the two halves. The claim is recorded in both shapes, through
one call, so a call site cannot record one without the other; and the file that
call writes is the one the production reader parses.
"""

import json
from pathlib import Path

from driver_maintenance_support import shell

DRIVER = Path(__file__).resolve().parents[1] / "src/argus/driver"

PHASE = 100042


def _reservation_file(tmp_path: Path) -> Path:
    return tmp_path / "repo" / "runs" / f"phase-{PHASE:06d}" / "reservation.json"


def test_a_recorded_claim_is_readable_by_the_fence_reader(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shell(
        tmp_path,
        f'phase_run_dir() {{ printf "%s\\n" "{root}/runs/phase-{PHASE:06d}"; }}\n'
        f"record_claim_fence {PHASE} rsv-fixture 3\n"
        'printf "claim=%s\\n" "$(cat "$CLAIM")"\n',
    )
    written = _reservation_file(tmp_path)
    assert json.loads(written.read_text()) == {
        "phase": f"{PHASE:06d}",
        "machine_id": "fixture",
        "reservation_id": "rsv-fixture",
        "generation": 3,
    }


def test_a_refused_claim_writes_no_reservation_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = shell(
        tmp_path,
        f'phase_run_dir() {{ printf "%s\\n" "{root}/runs/phase-{PHASE:06d}"; }}\n'
        "board_assert_owner() { return 1; }\n"
        "rc=0\n"
        f"record_claim_fence {PHASE} rsv-fixture 3 || rc=$?\n"
        'printf "rc=%s\\n" "$rc"\n',
    )
    assert "rc=1" in output
    assert not _reservation_file(tmp_path).exists()


def _without_the_wrapper() -> dict[str, str]:
    """Every driver source, with the one call the wrapper is allowed to make.

    ``record_claim_fence`` is the call site that may name ``record_claim``: it is
    the pairing this file exists to require. Its body is removed before the scan
    so the scan reads call sites rather than the definition of the rule.
    """
    sources = {path.name: path.read_text() for path in sorted(DRIVER.glob("*.sh"))}
    fence = sources["lib-driver-fence.sh"]
    head, _, rest = fence.partition("record_claim_fence() {")
    body, _, tail = rest.partition("\n}")
    sources["lib-driver-fence.sh"] = head + tail
    assert body, "record_claim_fence has no body to exempt"
    return sources


def test_no_driver_call_site_records_the_claim_alone() -> None:
    """A new call site must use the call that writes both shapes.

    ``record_claim`` on its own leaves the fenced steps with nothing to read, so
    the tick refuses itself one step later with a reason that names the fence it
    just took. The scan is the cheap half of that rule; the cases above are the
    expensive half, and neither alone would have caught the launch case.
    """
    offenders: list[str] = []
    for name, text in _without_the_wrapper().items():
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("record_claim "):
                offenders.append(f"{name}:{number}")
    assert offenders == [], (
        "these call sites record the claim without the reservation file every "
        f"fenced write path reads: {offenders}"
    )


def test_the_tick_claims_and_fences_before_it_cuts_a_branch() -> None:
    """The fence has to be down before the first step that proves it.

    ``_drive_cut_branch`` runs the fenced tidy and branch steps, so the claim and
    its reservation file must be written before the cut. It was this ordering
    that made the launch case refuse before it created a session.
    """
    text = (DRIVER / "lib-driver-drive.sh").read_text()
    claim_at = text.index('record_claim_fence "$number"')
    cut_at = text.index("_drive_cut_branch || return")
    assert claim_at < cut_at


def test_the_fence_library_writes_the_file_the_fences_read() -> None:
    """One writer, named in one place, and it writes the reader's filename."""
    fence = (DRIVER / "lib-driver-fence.sh").read_text()
    body = fence.split("record_phase_reservation() {", 1)[1].split("\n}", 1)[0]
    assert "reservation.json" in body


def test_reservation_ids_with_quotes_still_write_valid_json(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shell(
        tmp_path,
        f'phase_run_dir() {{ printf "%s\\n" "{root}/runs/phase-{PHASE:06d}"; }}\n'
        "PHASE_MACHINE_ID='mach\"ine'\n"
        f"record_phase_reservation {PHASE} 'rsv\"id' 3\n",
    )
    assert json.loads(_reservation_file(tmp_path).read_text()) == {
        "phase": f"{PHASE:06d}",
        "machine_id": 'mach"ine',
        "reservation_id": 'rsv"id',
        "generation": 3,
    }
