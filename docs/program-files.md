# Program Files

`bin/argus` is the development command and `cli.py` is the installed one. Both call the same shell driver.

The Python tools:

- `next_phase.py` and `work_source.py` select work, and `roadmap_source.py` is the
  roadmap work source: it re-exports the module surface (declared in `__all__`).
  The work sits in `roadmap_evidence.py` (the completion markers and the roadmap
  folder), `roadmap_queue.py` (the candidates and the next one),
  `roadmap_index.py` (index.md drift), `roadmap_group.py` (reservation-group
  declarations), `roadmap_select.py` (claiming a phase or a group on the board),
  `roadmap_server.py` (`select_server_phase` and the `RoadmapSource` class), and
  the `roadmap_selftest*.py` files (the self-test).
- `config.py` reads settings, and `conventions.py` shares them across tools.
- `phase_reservations.py` is the entry point of the reservation tools: it
  re-exports the module surface and runs `main`. The work sits in
  `phase_reservations_core.py` (statuses, errors, clock stamps, validation),
  `phase_reservations_state.py` (the board state file),
  `phase_reservations_git.py` and `phase_reservations_board.py` (git primitives,
  the public/private boundary, and the board branch),
  `phase_reservations_worktree.py` (short-lived worktrees and the
  read-modify-push transaction), `phase_reservations_records.py` (claim
  windows), the `phase_reservations_store*.py` files (the store and its role
  parts), `phase_reservations_files.py` (the reservation and publication
  files), `phase_reservations_cli.py` (the command line), and the
  `phase_reservations_selftest*.py` files (the self-test).
- `gitsync.py` is the entry point of the publishing tools: it parses the command
  line and re-exports `awaiting_review`. The work sits in `gitsync_git.py` (git
  primitives), `gitsync_paths.py` (branch names), `gitsync_merge.py` and
  `gitsync_sync.py` (one sync and both checkouts), `gitsync_branch.py` (placing a
  checkout on a phase branch), `gitsync_runstate.py` (leftover run state),
  `gitsync_receipts.py` and `gitsync_review.py` (receipts and open predecessors),
  `gitsync_publish.py`, `gitsync_group_publication.py` and
  `gitsync_publish_phase.py` (publication), `gitsync_tidy.py` (returning a
  checkout to the integration branch), and the `gitsync_mode*.py` and
  `gitsync_main.py` files that dispatch the modes.
- `phase_notifications.py` formats notices.
- `remedy_escalation.py` records open findings and releases a claim.
- `phase_group.py` requests a reservation group and answers with the array its owner now holds.

The driver is split across `lib-driver-*.sh`, one module per concern. `lib-driver-group.sh` holds the group side of a tick: the member array a selection carries, the one branch the group publishes on, and the renewal the tick loop owes a running group.

`exec_tempdir.py` is a copy of a shared test helper. It differs from that copy by the fixture-name prefix and by one behavior: this copy creates its build-tree fallback on first use, so a hardened host that mounts its temporary directories `noexec` still has somewhere to run a fixture. Re-copy it when the shared helper changes, and keep that difference.
