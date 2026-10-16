# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One configuration file for an Argus tick, and the CLI that renders it.

The file is JSON with `//` and `/* */` comments. Comments are removed with a
small scanner that respects JSON string literals and backslash escapes, so a
`//` inside a string stays part of the value. The escape rules follow RFC 8259,
section 7: a reverse solidus escapes the next character, so `\\"` does not end
the string and `\\\\` leaves the scanner ready to end it.

Every relative path is resolved against the folder that supplied the value:
the file's folder for a file value, the working directory for a flag.
Machine settings replace named values, and flags win for one run. `sources`
records file, overlay, flag, or optional issue default for each value.
A missing required key stops the load and names the key.
`machine.id-file` names a file whose first non-empty line is the machine's name,
so machines with the same layout can share one configuration file.
"""

import argparse
import json
import sys

if not __package__:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus.config.loader import Config, ConfigError, load, strip_comments
from argus.config.render import (
    _json_view,
    _render,
    _shell_value,
    report_lines,
    shell_lines,
)
from argus.config.schema import (
    _OBJECT_KEYS,
    _OVERRIDES,
    _REPORT_KEY,
    SCHEMA,
    SHELL_VARS,
    Field,
)

# The schema, the loader, and the renderers live in their own modules; these
# names stay importable here because the shell helpers, the other tools, and
# the tests import them from this path.
__all__ = [
    "Config",
    "ConfigError",
    "Field",
    "SCHEMA",
    "SHELL_VARS",
    "_OBJECT_KEYS",
    "_OVERRIDES",
    "_REPORT_KEY",
    "_json_view",
    "_render",
    "_shell_value",
    "load",
    "main",
    "report_lines",
    "shell_lines",
    "strip_comments",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="argus.config.cli",
        description="Load an Argus configuration and print it for a caller.",
    )
    parser.add_argument("--config", help="config file; defaults to ./config.json")
    parser.add_argument("--repo-root")
    parser.add_argument("--phases-dir")
    parser.add_argument("--phase-glob", action="append")
    parser.add_argument("--driver")
    parser.add_argument(
        "--format", choices=("shell", "json", "report"), default="report"
    )
    args = parser.parse_args(argv)

    overrides: dict[str, object] = {}
    if args.repo_root is not None:
        overrides["repo-root"] = args.repo_root
    if args.phases_dir is not None:
        overrides["phases-dir"] = args.phases_dir
    if args.phase_glob is not None:
        overrides["phase-glob"] = args.phase_glob
    if args.driver is not None:
        overrides["driver"] = args.driver

    try:
        config = load(args.config, overrides)
    except ConfigError as exc:
        print(f"argus config: {exc}", file=sys.stderr)
        return 2

    if args.format == "shell":
        for line in shell_lines(config):
            print(line)
    elif args.format == "json":
        print(json.dumps(_json_view(config), indent=2))
    else:
        for line in report_lines(config):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
