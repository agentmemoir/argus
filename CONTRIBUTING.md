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
make leakscan      # the published tree carries no host path, credential, or run state
make example-check # the bundled example loads and passes the readiness report
make self-test     # the driver, the reservation group, and the sync self-tests
make guard-faults  # break every launch guard and require a named failure
```

`make wheel` builds the package and checks that the driver ships inside.
CI runs the lint and format checks, `make leakscan`, `make example-check`, `make self-test`, and `make guard-faults`.

## Rules That Matter Here

- **One formatter: `ruff format`, 88 columns.** Never hand-format.
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
