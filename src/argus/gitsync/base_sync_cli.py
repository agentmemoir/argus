# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The base-sync command: one checkout and one recorded branch, one exit code.

Exit codes: 0 no conflict, or merged and pushed; 1 a read, the merge, or the push
failed; 2 a configuration or usage error; 3 a text conflict is in progress for an
agent to resolve, then commit and push.
"""

import argparse
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from argus import (
        _bootstrap as _bootstrap,  # noqa: F401 - register the source package
    )
from argus import conventions
from argus.config.loader import ConfigError as ArgusConfigError
from argus.gitsync.base_sync import EXIT_CODES, base_sync, report_lines
from argus.gitsync.git import ConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge the base into a PR branch.")
    parser.add_argument("--repo", required=True, help="the checkout to check")
    parser.add_argument(
        "--pr-branch", required=True, help="the branch the run recorded"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # The driver also passes its configuration flags; parse_known_args skips them.
    args, _unknown = build_parser().parse_known_args(argv)
    try:
        out = base_sync(args.repo, args.pr_branch)
    except ConfigError as exc:
        print(f"base-sync: configuration error: {exc}")
        return 2
    for line in report_lines(out):
        print(line)
    return EXIT_CODES[out["state"]]


if __name__ == "__main__":
    try:
        conventions.configure_from_cli(sys.argv[1:])
    except ArgusConfigError as exc:
        print(f"base-sync: configuration error: {exc}")
        sys.exit(2)
    sys.exit(main())
