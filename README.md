# Argus

Argus runs an unattended work queue. A tick selects work, starts its pipeline in tmux, and watches the run. When a board is configured, it reserves the work on that shared Git board first. A blocked or failed run writes a halt marker, and a later tick checks published work before it starts more work. A remedy loop that ran out of rounds with findings still open is the one exception: that run ends `remedy_escalated`, records the open findings for a person, releases its claim, and the line continues to the next phase.

This folder holds the program and its own tools. It can be copied into a repository of its own. It does not carry Glide, stages, pipelines, or project scripts.

## Run

Argus reads one configuration file per tick. The file names every value the tick uses, including the commands it runs. The driver needs Bash, Git, tmux, and a Python with PyYAML; the publisher uses the configured `gh`; notifications are optional.

```sh
make install                     # link this working tree onto PATH
argus --config ~/etc/project-argus.jsonc --check
argus --config ~/etc/project-argus.jsonc --dry-run
```

`make install` links the source command into `~/.local/bin`. Add that folder to `PATH`. Use `BINDIR` to choose another folder. Edits in this tree are live.

`pip install .` and `uv tool install .` install Argus as a package with a console script. The package ships the shell driver and the helpers beside it, because the command *is* the driver. `make wheel` builds it and checks that the driver is inside.

The modes are `--check` (the readiness report), `--dry-run` (what the tick would do, without launching), `--self-test` (the driver's own test, which needs no configuration file), `--stop` (halt the live run), and `--session <NUMBER> <phase-file>` (run one phase inside tmux).

## Configuration

A tick reads one JSONC file: pass its path with `--config`, or leave the flag out and put `config.json` in the current folder. `config.sample.jsonc` is the schema, with a comment on each value. No value has a default in code: a missing required key stops the tick and names the key, and no environment variable is read. A relative path resolves against the folder that holds the file; an absolute path is used as written. `board.file` is a path inside the configured board checkout.

The values:

- `repo-root` — the project checkout; required.
- `pipeline` — the pipeline file a tick runs; required.
- `roadmap-dir` — the folder holding the work-item files; required.
- `number-input`, `file-input` — the pipeline input names that carry the work-item number and file; optional, but a pipeline that declares those inputs needs them named.
- `driver.provider` — the work source; required.
- `driver.include` — the work-item file globs; required. `driver.exclude` — file names to ignore; optional.
- `driver.runs` — the run-state folder; required. `driver.state` — the folder inside `driver.runs` that holds the driver's own log, lock, halt marker, and pid file; required.
- `driver.stale-after`, `driver.outage-stale`, `driver.quiet-minutes`, `driver.keep-logs-days`, `driver.merge-check-max`, `driver.recover-own-claim` — the driver's bounds; required.
- `machine.id-file` — a file whose first non-empty line is the machine's name, recorded as the owner of a claim; required. A relative path resolves against the config file, so each machine keeps its own file while the rest of the configuration is shared.
- `commands.glide`, `commands.git`, `commands.tmux`, `commands.python`, `commands.hermes`, `commands.gh` — the external commands, named rather than searched for; required.
- `tmux.session`, `tmux.width`, `tmux.height`, `tmux.idle-window`, `tmux.phase-window`, `tmux.idle-script`, `tmux.refresh` — the tmux session and its geometry; required.
- `board.repo`, `board.file`, `board.branch`, `board.remote` — the shared board; required only when the `board` object is present.
- `notify.profile`, `notify.channel`, `notify.disabled` — the notification target; required when the `notify` object is present. `notify.fallback`, `notify.heartbeat` — optional.

The four override flags `--driver`, `--repo-root`, `--roadmap-dir`, and repeatable `--phase-glob` beat the file for one run. The readiness report prints every value with the flag-or-file source it came from.

## Paths and ownership

The pipeline names each phase run directory. The run directory holds `run.json`, `ledger.json`, the reservation token, and `publication.json`. There is no Argus run-directory flag; the pipeline's `run_dir` key owns the value.

Publication commits run state in the configured board checkout, so a publishing pipeline puts its run directory there. A run directory outside that checkout is refused before staging. The configured run folder holds the driver log, lock, and halt marker.

The board branch is never checked out. Reading the board does not create it; an explicit migration or the first write creates it from the remote integration branch. A board is shared: every machine that reserves work on it names the same `board.file` and `board.branch`.

A phase document may declare a reservation group: several phases one owner runs as one unit. The whole group is claimed in one board write under one reservation id, its members run one per tick on one branch, and one pull request covers them all, while each member keeps its own receipt under its own run directory. The tick renews the group while a member runs, `--release` gives the whole group back, and a member that is already published is never undone by a release or an expiry.

The configured `glide` command runs the pipeline as `glide run --pipeline <file> --input <name>=<value>`. A missing command stops the tick before a claim. Argus names no Glide source tree, sets no `PYTHONPATH`, and keeps no fallback copy. The pipeline's stages and hooks are project content, so Argus needs no project `scripts/` folder.

Argus does not search for project folders. The config file names every project path, so a missing path stops the tick with that path in the error.

Work-item files end in `.md`. The first number separated by `-` or `_` is the phase number, and the text after it is the branch slug. Appendix files are skipped. For example, `work-100010-cache.md` matches an include glob of `work-*.md`.

## Program files

`bin/argus` is the development command and `cli.py` is the installed one. Both call the same shell driver. The Python tools sit in role packages under `src/argus/`, each one a namespace package: `select/` picks the work (`next_phase.py`, `work_source.py`, `policy.py`, `group.py`), `roadmap/` is the roadmap work source (`source.py`), `board/` owns the board, the claim and the remedy record (`reservations.py`), `gitsync/` syncs checkouts and checks publication (`main.py`), `config/` reads settings (`cli.py`), and `notify/` formats notices (`notifications.py`). `conventions.py` and `exec_tempdir.py` sit at the package root.

The driver and its shell helpers sit in `driver/`: `phase-driver.sh` is the entry point and `lib-driver-*.sh` is one module per concern. `driver/lib-driver-group.sh` holds the group side of a tick: the member array a selection carries, the one branch the group publishes on, and the renewal the tick loop owes a running group.

`exec_tempdir.py` is a copy of a shared test helper. It differs from that copy by the fixture-name prefix and by one behavior: this copy creates its build-tree fallback on first use, so a hardened host that mounts its temporary directories `noexec` still has somewhere to run a fixture. Re-copy it when the shared helper changes, and keep that difference.

## Checks

```sh
make self-test      # every Python check, including the identifier gate
make check          # the readiness report; needs CONFIG, default config.json
make dry-run        # what the next tick would do; needs CONFIG, default config.json
make guard-faults
```

`make self-test` builds temporary checkouts and a board. The entry point check uses a config file with paths outside the source tree. It compares selection, halt markers, and notices across both commands. `test_identifiers.py` checks that account IDs and local paths do not reach the package. The guard suite changes each guard on purpose, requires a named failure, restores the file, and checks its digest. It includes an entry point fault, and `--order reverse` or `--order shuffle` checks that the result does not depend on the order.

Three group cases run a reservation group on their own throwaway checkout: one group that runs, one released with a member already finished, and one released while a member is running.

The readiness report runs Argus's own self-tests and prints the loaded configuration.

The test pipeline uses a fake Glide command. These checks do not run an agent or publish to GitHub. A real deployment still needs a configured Glide command, pipeline stages, Git remotes, and credentials.
