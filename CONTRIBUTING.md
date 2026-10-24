# Contributing

Thanks for taking a look at argus. The contribution path is short.

## Toolchain

`uv` is the toolchain. Do not introduce pip, poetry, or conda instructions:

```sh
uv sync               # runtime plus the dev dependency group, from uv.lock
uv run pytest tests   # the unit suites
```

Everything runs through `uv run`; there is no virtualenv to activate. Never hand-edit `uv.lock`.

## The Local Gate

Run the checks before opening a pull request. `make quick` runs the lint, format, type, line-limit, and prose checks in one command:

```sh
make quick         # every static check below, no tests
make lint          # ruff check
make fmt-check     # formatting, 88 columns
make typecheck     # mypy over the package
make typecheck-strict # pyright strict over the package and the suite
make lines         # the 350-line per-file limit
make prose         # one paragraph per line in prose files
make changelog-check # changelog fragments: names, categories, one-line paragraphs
make leakscan      # the published tree carries no host path, credential, or run state
make example-check # the bundled example loads and passes the readiness report
make install-check # the built wheel installs and its console script runs
make self-test     # CI only: the driver, the reservation group, and the sync self-tests
make guard-faults  # CI only: break every launch guard and require a named failure
```

`make self-test` and `make guard-faults` take many minutes, so they run in CI. Outside CI (when the `CI` variable is unset) they stop at once with a one-line message. Set `ARGUS_ALLOW_LOCAL=1` to run one locally anyway. `uv run python tests/guard_faults.py --only NAME` runs a single fault and is not blocked.

`make wheel` builds the package and checks that the driver ships inside; `make install-check` installs that wheel into a clean environment and runs the installed console script. CI runs the lint, format, type, line-limit, prose, and changelog-fragment checks, `make leakscan`, `make example-check`, `make install-check`, `make self-test`, and `make guard-faults`.

## Rules That Matter Here

- **One formatter: `ruff format`, 88 columns.** Never hand-format.
- **Types: mypy at its default level over the package, pyright strict over the package and the suite.** `make typecheck` and `make typecheck-strict` must pass. A shipped `py.typed` is the open step of the recorded target.
- **No Python file may exceed 350 lines**, comments and blank lines included. `make lines` must pass. New files must fit; the files still above the limit are listed in the Makefile and shrink one per session.
- **Argus names no project layout.** Every path and command comes from the configuration file; a missing required key stops the tick instead of a guessed default.
- **Apply the rules to new and refactored code.** Do not bury a refactor in a formatter diff, and do not mass-reformat in the same change as a behaviour fix.

## Changelog

Do not edit `CHANGELOG.md` on a pull request. Add one fragment per change under `changelog.d/`, named `<slug>.<category>.md`, for example `fix-stale-lock.fixed.md`.

The slug is lowercase words joined by hyphens, and it must be unique in `changelog.d/`. The category is one of `added`, `changed`, `deprecated`, `removed`, `fixed`, or `security`.

The file holds one paragraph, on one line. `make changelog-check` fails on a bad name, an empty file, or a paragraph spread over lines.

At release, `uv run python scripts/changelog.py build <version>` writes the fragments into `CHANGELOG.md` under a version heading, in category order, and deletes them.

## Checks

`make self-test` builds temporary checkouts and a board. `make guard-faults` changes each launch guard on purpose, requires a named failure, and restores the file. Both are hermetic: no agent runs, and nothing is published. Add a check with any behaviour change.

## Commits and Leaks

This is a public repository. Before you push, confirm your change carries nothing private: no host paths, no machine names, no secrets, and no run state. `make leakscan` checks the published tree for those shapes. Pushing is a separate, explicit step.
