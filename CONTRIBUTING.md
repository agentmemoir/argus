# Contributing

Thanks for taking a look at argus. The contribution path is short.

## Toolchain

`uv` is the toolchain. Do not introduce pip, poetry, or conda instructions:

```sh
uv sync               # runtime plus the dev dependency group, from uv.lock
uv run pytest tests   # the unit suites
```

Everything runs through `uv run`; there is no virtualenv to activate. Never
hand-edit `uv.lock`.

## The Local Gate

Run the checks before opening a pull request:

```sh
make lint          # ruff check
make fmt-check     # formatting, 88 columns
make typecheck     # mypy over the package
make typecheck-strict # pyright strict over the package and the suite
make lines         # the 350-line per-file limit
make leakscan      # the published tree carries no host path, credential, or run state
make example-check # the bundled example loads and passes the readiness report
make install-check # the built wheel installs and its console script runs
make self-test     # the driver, the reservation group, and the sync self-tests
make guard-faults  # break every launch guard and require a named failure
```

`make wheel` builds the package and checks that the driver ships inside;
`make install-check` installs that wheel into a clean environment and runs the
installed console script.
CI runs the lint, format, type, and line-limit checks, `make leakscan`, `make example-check`, `make install-check`, `make self-test`, and `make guard-faults`.

## Rules That Matter Here

- **One formatter: `ruff format`, 88 columns.** Never hand-format.
- **Types: mypy at its default level over the package, pyright strict over the
  package and the suite.** `make typecheck` and `make typecheck-strict` must
  pass. A shipped `py.typed` is the open step of the recorded target.
- **No Python file may exceed 350 lines**, comments and blank lines included.
  `make lines` must pass. New files must fit; the files still above the limit
  are listed in the Makefile and shrink one per session.
- **Argus names no project layout.** Every path and command comes from the
  configuration file; a missing required key stops the tick instead of a
  guessed default.
- **Apply the rules to new and refactored code.** Do not bury a refactor in a
  formatter diff, and do not mass-reformat in the same change as a behaviour
  fix.

## Checks

`make self-test` builds temporary checkouts and a board. `make guard-faults`
changes each launch guard on purpose, requires a named failure, and restores
the file. Both are hermetic: no agent runs, and nothing is published. Add a
check with any behaviour change.

## Commits and Leaks

This is a public repository. Before you push, confirm your change carries
nothing private: no host paths, no machine names, no secrets, and no run
state. `make leakscan` checks the published tree for those shapes. Pushing is
a separate, explicit step.
