# Changelog

Notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project intends to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html); no version has
been released yet.

## [Unreleased]

- `argus` ticks an unattended work queue: select a phase, reserve it on a shared
  Git board, launch its pipeline in tmux, watch the run, and check publication
  before the next tick.
- One JSONC configuration file names every path and command; a missing required
  key stops the tick and names the key.
- Reservation groups: several phases claimed in one board write, run on one
  branch, and published under one pull request, with the claim renewed while a
  member runs.
- Halt markers on blocked or failed runs, claim cooldown and expiry handling,
  and a remedy escalation that records open findings and releases the claim.
- Modes: `--check`, `--dry-run`, `--self-test`, `--stop`, and
  `--session <NUMBER> <PHASE-FILE>`.
- Checks: the driver self-test, the guard-faults harness, and a wheel that
  ships the shell driver.
- A publication lock left behind by a publish that died stops blocking after the
  30 minute heartbeat window, so claim recovery and other phases' publishes
  proceed. A fresh lock still blocks both; `release-publication` still frees one
  early.
- A relative `--repo-root` or `--roadmap-dir` resolves against the working
  directory, not the configuration file's folder, so `--root .` from the project
  root names the project root.
- The tmux idle watcher ends when the process that started it is gone, so one
  run outside tmux, or after its tmux server died, no longer loops as an orphan.
