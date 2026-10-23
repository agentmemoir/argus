# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Run the pipeline's ``pre_run`` hook for the readiness report (``--check``).

Glide runs this hook before every run, from the repository root, and refuses
the run when it exits nonzero. This module resolves the same command the way
Glide does (the host override file wins over the pipeline's ``hooks`` block) and
runs it once, so a refusing hook fails ``--check`` before the first tick.

Glide passes no flag or variable that marks a check call, so the hook runs for
real here. A ``pre_run`` hook must therefore be read-only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import yaml

POINT = "pre_run"
HOOK_TIMEOUT_S = 300.0
ACTION = "action: fix the pre_run hook so it exits 0, then run --check again"


class HookError(Exception):
    """The pre_run hook declaration cannot be read."""


def as_argv(value: object) -> tuple[str, ...]:
    """Return a hook declaration as an argument list, as Glide does.

    An empty list means no hook. A bare string is one argument.
    """
    if isinstance(value, str):
        parts: list[object] = [value]
    elif isinstance(value, list):
        parts = cast("list[object]", value)
    else:
        raise HookError(f"{POINT} must be a string or a list of strings")
    if not all(isinstance(part, str) and part for part in parts):
        raise HookError(f"{POINT} must be a string or a list of non-empty strings")
    return tuple(cast("list[str]", parts))


def default_override_path() -> Path:
    """Return Glide's host override file, as Glide resolves it."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "glide" / "hooks.toml"


def resolve_pre_run(pipeline: Path, override: Path) -> tuple[str, ...]:
    """Return the effective pre_run command: the override wins over the pipeline."""
    try:
        data: object = yaml.safe_load(pipeline.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HookError(f"cannot read pipeline {pipeline.name}") from exc
    if not isinstance(data, dict):
        raise HookError("pipeline must be a mapping")
    declared: object = cast("dict[str, object]", data).get("hooks") or {}
    if not isinstance(declared, dict):
        raise HookError("pipeline hooks must be a mapping")
    hooks = cast("dict[str, object]", declared)
    argv = as_argv(hooks[POINT]) if POINT in hooks else ()
    if override.is_file():
        try:
            parsed = tomllib.loads(override.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise HookError(f"hook override {override.name} is unusable") from exc
        block: object = parsed.get("hooks")
        if block is not None and not isinstance(block, dict):
            raise HookError(f"hook override {override.name}: [hooks] must be a table")
        if block:
            table = cast("dict[str, object]", block)
            if POINT in table:
                argv = as_argv(table[POINT])
    return argv


def refusal(detail: str) -> str:
    return f"{POINT} hook: REFUSED ({detail})\n{ACTION}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the pre_run hook for --check.")
    parser.add_argument("--pipeline", required=True, type=Path)
    parser.add_argument("--override", type=Path, default=None)
    args = parser.parse_args(argv)
    pipeline: Path = args.pipeline
    try:
        command = resolve_pre_run(pipeline, args.override or default_override_path())
    except HookError as exc:
        print(refusal(str(exc)))
        return 1
    if not command:
        return 0
    # The context Glide gives a run's hooks. A check has no run yet, so the run
    # directory and run id are not set.
    env = {
        **os.environ,
        "GLIDE_HOOK_POINT": POINT,
        "GLIDE_PIPELINE": str(pipeline),
        "GLIDE_STATE": "starting",
    }
    try:
        result = subprocess.run(  # noqa: S603 - argument list, never a shell string
            list(command),
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=HOOK_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(refusal(f"exceeded its {HOOK_TIMEOUT_S:.0f} second deadline"))
        return 1
    except OSError as exc:
        print(refusal(f"could not start: {exc.strerror or exc}"))
        return 1
    if result.returncode != 0:
        stderr = result.stderr or ""
        lines = [line for line in stderr.splitlines() if line.strip()]
        first = lines[0].strip() if lines else "no message on stderr"
        print(refusal(f"exit {result.returncode}: {first}"))
        return 1
    print(f"{POINT} hook: accepted (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
