# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A refused release drops the driver lock it took.

``--release`` takes the driver lock (fd 9) so it cannot race a running tick. Every
return path has to close that fd: a shell that keeps it still holds the flock, so
the operator's next driver command in the same process is refused by a lock
nothing else holds. The board-refusal return was the one path that did not.

The probe is the fd itself, and the script proves the probe measures before it
measures the fix: it opens fd 9 the way ``driver_lock_wait`` does and checks the
probe sees it open, then runs the refused release and checks it sees it closed.
The second probe asks a fresh process for the same lock, which is the harm the
leaked fd causes.
"""

from pathlib import Path

from driver_maintenance_support import shell


def test_a_refused_release_closes_the_driver_lock(tmp_path: Path) -> None:
    body = (
        "printf '100020\\n' >\"$CURRENT\"\n"
        "printf '100020\\trsv-foreign\\t7\\n' >\"$CLAIM\"\n"
        # The same fd the real driver_lock_wait opens, without needing flock: the
        # fd is what the leak is about.
        'driver_lock_wait() { mkdir -p "$RUN_DIR"; exec 9>"$LOCK"; }\n'
        "driver_lock_wait\n"
        "if : >&9 2>/dev/null; then printf 'before=open\\n'; fi\n"
        "board_assert_owner() { log ASSERT; return 1; }\n"
        "rc=0; release || rc=$?\n"
        "printf 'release_rc=%s\\n' \"$rc\"\n"
        "if : >&9 2>/dev/null; then printf 'after=open\\n'; else printf 'after=closed\\n'; fi\n"
        # A second driver command in the same shell: a fresh process asking for
        # the same lock must win it, which it cannot while this shell holds fd 9.
        "if command -v flock >/dev/null 2>&1; then\n"
        "  if flock -n \"$LOCK\" true 2>/dev/null; then printf 'lock=free\\n'\n"
        "  else printf 'lock=held\\n'; fi\n"
        "fi\n"
    )

    output = shell(tmp_path, body)

    assert "before=open" in output, "the fd probe did not see a held fd 9"
    assert "release_rc=1" in output, output
    assert "ASSERT" in (tmp_path / "events").read_text().splitlines()
    assert "after=closed" in output, "the refused release left fd 9 open"
    assert not (tmp_path / "release-checkpoint.json").exists()
    assert (tmp_path / "claim").is_file()
    if "lock=" in output:
        assert "lock=free" in output, "a second process could not take the lock"
