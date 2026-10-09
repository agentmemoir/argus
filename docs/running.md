# Running Argus

One configuration file per tick, and one command. The file names every value the tick uses, including the commands it runs.

Argus needs Bash, Git, tmux, and a Python with PyYAML. The publisher uses the configured `gh`; notifications are optional.

## Install

```sh
make install                     # link this working tree onto PATH
argus --config ~/etc/project-argus.jsonc --check
argus --config ~/etc/project-argus.jsonc --dry-run
```

`make install` links the source command into `~/.local/bin`. Add that folder to `PATH`. Use `BINDIR` to choose another folder. Edits in the tree are live.

`pip install .` and `uv tool install .` install Argus as a package with a console script. The package ships the shell driver and the helpers beside it, because the command *is* the driver. `make wheel` builds it and checks that the driver is inside.

`bin/argus` is the development command, and the installed `argus` console script is the packaged one. Both call the same shell driver.

## Modes

Pass one mode flag:

- `--check` — the readiness report. It prints every configured value with the flag-or-file source it came from, checks the commands and folders the tick will use, runs Argus's own self-tests, and exits nonzero when it finds a problem.
- `--dry-run` — what the tick would do, without launching anything.
- `--self-test` — the driver's own test. It needs no configuration file and starts no agent.
- `--stop` — halt the live run.
- `--release` — stop the live run and give its reservation group back.
- `--session <NUMBER> <PHASE_FILE>` — run one phase inside tmux.

With no mode flag, a tick runs once.

## The Configuration File

`--config` names the file. Without the flag, `config.json` in the current folder is used when it is there. `--self-test` reads no file.

Four flags override the file for one run: `--driver`, `--repo-root`, `--roadmap-dir`, and repeatable `--phase-glob`. The readiness report prints which of the flag and the file supplied each value. [configuration.md](configuration.md) covers the values.
