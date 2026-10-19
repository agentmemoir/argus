# Argus Documentation

Argus runs an unattended work queue. A tick selects work, starts its pipeline in tmux, watches the run, reserves the work on a shared Git board when one is configured, and publishes the result.

These pages cover all of Argus. Start with the [project README](../README.md) for what Argus is and how to install it, then the pages below.

## Reference

| doc | what it covers |
|---|---|
| [running.md](running.md) | Install, invocations, run modes, and the one-run override flags. |
| [configuration.md](configuration.md) | Every configuration key, and the rules for reading the file. |
| [paths-and-ownership.md](paths-and-ownership.md) | Run directories, publication, the board, and reservation groups. |
| [program-files.md](program-files.md) | The map of the files inside the program folder. |
| [checks.md](checks.md) | The checks, and what each one proves. |
| [issues.md](issues.md) | The removed GitHub issue work source. |

## Worked Example

[`examples/`](../examples/) holds a small configuration and the project it points at, with a walkthrough in [examples/README.md](../examples/README.md).

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the toolchain and the local gate.
