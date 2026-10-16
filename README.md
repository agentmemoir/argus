# Argus

Argus is an unattended phase driver for developers who run coding-agent pipelines without a person at the keyboard. A tick selects one work item, reserves it on a shared Git board when one is configured, launches the pipeline in tmux, watches the run, and publishes the result.

A blocked or failed run writes a halt marker, and a later tick checks published work before it starts more. When a phase's remedy loop runs out of rounds with findings still open, that run ends `remedy_escalated`, records the open findings for a person, and releases its claim, so the line continues to the next phase.

## Install

Argus needs Bash, Git, tmux, and a Python with PyYAML. The publisher uses the `gh` you configure; notifications are optional.

```sh
make install                     # link this working tree onto PATH
argus --config ~/etc/project-argus.jsonc --check
```

`make install` links the source command into `~/.local/bin`; add that folder to `PATH`. Use `BINDIR` to choose another folder. Edits in the tree are live. `pip install .` and `uv tool install .` install Argus as a package with a console script instead, and `make wheel` builds that package.

## Quickstart

The repository ships a worked example, so you can read a real readiness report before you write a configuration of your own:

```sh
make example-check
# → prints the readiness report for examples/argus-config.jsonc
```

`make example-check` loads the bundled configuration and runs the readiness report. Exit status is the verdict. It needs no Glide installed, because the report only asks whether the configured command resolves.

## Why Argus?

Reach for Argus when you already run a coding-agent pipeline and want the work to move without a person at the keyboard. It owns the loop around your pipeline: picking the next item, holding a claim so two machines never run the same item, watching for a stall, and publishing the finished branch. Your pipeline, its stages, and its hooks stay project content, so Argus needs no project `scripts/` folder.

## Documentation

Full documentation is in [`docs/`](docs/README.md). Beyond a plain run, the modes are `--check`, `--dry-run`, `--self-test`, `--stop`, `--release`, `--resume [NUMBER]`, `--session NUMBER FILE`, `--merge-check-only`, and `abandon NUMBER [--reason TEXT]`.

| I want to… | Go to |
|---|---|
| Install, run, and use the modes and the one-run override flags | [docs/running.md](docs/running.md) |
| Configure a tick | [docs/configuration.md](docs/configuration.md) |
| Understand run directories, the board, and reservation groups | [docs/paths-and-ownership.md](docs/paths-and-ownership.md) |
| Map the program files | [docs/program-files.md](docs/program-files.md) |
| Work the GitHub issue source | [docs/issues.md](docs/issues.md) |
| Run the checks | [docs/checks.md](docs/checks.md) |
| Follow a worked example | [examples/README.md](examples/README.md) |

## Contributing and license

PRs are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems through [SECURITY.md](SECURITY.md). Argus carries the [Apache License 2.0](LICENSE).
