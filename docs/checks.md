# Checks

Run the checks from the repository root. The tooling runs through `uv run`; the driver's own checks call the shell driver directly.

| command | what it proves |
|---|---|
| `make lint` | `ruff check` finds nothing. |
| `make fmt-check` | The tree is formatted at 88 columns. |
| `make typecheck` | mypy over the package, at its default level. |
| `make typecheck-strict` | pyright in strict mode over the package and the suite, the checker behind the editor's Pylance. |
| `make llc` | No source file exceeds its line limit: 350 for Python, 450 for every other scanned language. |
| `make self-test` | The driver, configuration, issue source, issue board, issue publishing, reservation group, gitsync, and identifier checks pass. Needs no configuration file. |
| `make identifiers` | The published folder carries no home path, no numeric account or channel id, and no notification default or literal that is not a placeholder. |
| `make leakscan` | The published tree carries no host path, no credential assignment, and no run state. |
| `make example-check` | The bundled example configuration loads and passes the readiness report. |
| `make install-check` | The built wheel installs into a clean venv, every shipped module imports, and the installed console script passes the example readiness report. |
| `make guard-faults` | Every launch guard, broken on purpose, produces its named failure. |
| `make wheel` | The built wheel ships the driver and its helpers, and the checker validates its contents. |
| `make check` | The readiness report against `CONFIG` (default `config.json`). |
| `make dry-run` | What the next tick would do, without launching. |

`make self-test` and `make guard-faults` build throwaway checkouts and a board; no agent runs and nothing is published. `make guard-faults` changes each guard in the source on purpose, requires the named failure, restores the file, and checks its digest. `--only NAME` narrows it to one fault, and `--order reverse` or `--order shuffle` checks that the result does not depend on order.

Issue faults cover trust, delimiter escaping, text caps, preference validation, task containment, launch preflight, and the separate board file. They run only the affected source, driver, or board check.

`make leakscan` covers tracked files plus untracked files that git does not ignore, so a leak fails before it is committed. `make example-check` puts a stand-in `glide` command first on `PATH`, because the readiness report only asks whether the configured command resolves.

CI runs the lint, format, type, and line-limit checks, then `make leakscan`, `make example-check`, `make install-check`, `make self-test`, and `make guard-faults`, on every pull request. [CONTRIBUTING.md](../CONTRIBUTING.md) carries the local gate and the commit rules.
