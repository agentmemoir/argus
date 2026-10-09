# Program Files

`bin/argus` is the development command and `cli.py` is the installed one. Both call the same shell driver.

The Python tools:

- `next_phase.py`, `work_source.py`, and `roadmap_source.py` select work.
- `config.py` reads settings, and `conventions.py` shares them across tools.
- `phase_reservations.py` owns the board.
- `gitsync.py` syncs checkouts and checks publication.
- `phase_notifications.py` formats notices.
- `remedy_escalation.py` records open findings and releases a claim.
- `phase_group.py` requests a reservation group and answers with the array its owner now holds.

The driver is split across `lib-driver-*.sh`, one module per concern. `lib-driver-group.sh` holds the group side of a tick: the member array a selection carries, the one branch the group publishes on, and the renewal the tick loop owes a running group.

`exec_tempdir.py` is a copy of a shared test helper. It differs from that copy by the fixture-name prefix and by one behavior: this copy creates its build-tree fallback on first use, so a hardened host that mounts its temporary directories `noexec` still has somewhere to run a fixture. Re-copy it when the shared helper changes, and keep that difference.
