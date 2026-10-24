# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared settings, paths, and phase names for Argus tools."""

import contextlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from argus.config.loader import Config

_settings: dict[str, object] = {}
_MISSING = object()


def configure(settings: Mapping[str, Any]) -> dict[str, object]:
    """Replace the settings used by the current tool invocation."""
    global _settings
    _settings = dict(settings)
    return _settings


def override(settings: Mapping[str, Any]) -> dict[str, object]:
    """Apply explicit settings to the current invocation."""
    previous = _settings
    configure({**_settings, **settings})
    return previous


def settings_from_config(config: "Config") -> dict[str, object]:
    """Return the validated values loaded by ``argus.config``."""
    return dict(config.values)


def configure_from_file(
    config_path: str | Path | None = None,
    overrides: dict[str, object] | None = None,
) -> "Config":
    """Load one config file and publish its values to shared settings."""
    from argus.config.loader import load

    config = load(config_path, overrides)
    configure(settings_from_config(config))
    return config


def configure_from_args(args: Any) -> "Config":
    """Load settings and the four shared CLI overrides from parsed arguments."""
    overrides: dict[str, object] = {}
    for key, name in (
        ("repo-root", "config_repo_root"),
        ("phases-directory", "config_phases_directory"),
        ("phase-glob", "config_phase_glob"),
        ("driver", "config_driver"),
    ):
        value = getattr(args, name, None)
        if value is not None:
            overrides[key] = value
    return configure_from_file(args.config, overrides)


def configure_from_cli(argv: list[str]) -> "Config":
    """Load shared settings from CLI flags embedded in a helper invocation."""
    import argparse

    # Exact flag names only. A caller's own flag, such as --repo, must not match
    # --repo-root by prefix and set the repo root.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--config")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--phases-directory", dest="config_phases_directory")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    args, _unknown = parser.parse_known_args(argv)
    return configure_from_args(args)


def setting(key: str, default: Any = _MISSING) -> Any:
    """Read one configured value, with defaults only for optional fields."""
    if key in _settings:
        return _settings[key]
    if default is not _MISSING:
        return default
    raise ValueError(f"configuration key '{key}' is not loaded")


def board_repo(repo: str | Path | None = None) -> Path | None:
    """The configured checkout that owns the reservation board."""
    path = setting("board.repo", None)
    if path is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path.resolve()


def runstate_repo(repo: str | Path | None = None) -> Path | None:
    """The configured checkout that owns the run state, when one is named."""
    path = setting("runstate.repo", None)
    if path is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path.resolve()


def phases_repo(repo: str | Path | None = None) -> Path | None:
    """The configured checkout that owns the phases."""
    path = setting("phases.repo", None)
    if path is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path.resolve()


def phases_folder(
    repo: str | Path | None = None, configured: str | Path | None = None
) -> Path | None:
    """The folder Argus reads phases from and writes their index to.

    An explicit ``configured`` folder is the operator's word and is never
    silently replaced. Otherwise the configured phases repository holds the
    configured directory. ``None`` means no phases repository is configured,
    which only the issue source reaches.
    """
    if configured is not None:
        base = Path(configured)
        return base if base.is_absolute() else Path(repo or setting("repo-root")) / base
    directory = setting("phases.directory", None)
    if directory is None:
        return None
    folder = Path(directory)
    if folder.is_absolute():
        return folder
    owner = phases_repo(repo)
    if owner is None:
        return Path(repo or setting("repo-root")) / folder
    return owner / folder


def require_phases_folder(
    repo: str | Path | None = None, configured: str | Path | None = None
) -> Path:
    """The phases folder, or an error when no phases repository is configured."""
    folder = phases_folder(repo, configured)
    if folder is None:
        raise ValueError("no phases repository is configured")
    return folder


def phase_globs() -> list[str]:
    """The configured work-item filename patterns."""
    return list(setting("driver.include"))


def phase_excluded(name: str) -> bool:
    """True when ``name`` matches a configured excluded file name."""
    return Path(name).name in set(setting("driver.exclude", []))


def phase_paths(folder: str | Path) -> list[Path]:
    """Every configured work-item file, in stable order and without repeats."""
    found: set[Path] = set()
    for pattern in phase_globs():
        found.update(Path(folder).glob(pattern))
    return [path for path in sorted(found) if not phase_excluded(path.name)]


def runs_dir(repo: str | Path | None = None) -> Path:
    """The run-state folder named in the loaded config."""
    path = Path(setting("driver.runs"))
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path


def phase_file_parts(path: str | Path) -> tuple[int, str] | None:
    """Read the first numeric filename token and the slug after it.

    Tokens are separated by '-' or '_'. The prefix is not part of the slug.
    Appendix documents and files without a number and slug are not phases.
    """
    name = Path(path)
    if name.suffix != ".md":
        return None
    match = re.search(r"(?:^|[-_])(\d+)[-_](.+)$", name.stem)
    if match is None or match.group(2).startswith(("appendix-", "appendix_")):
        return None
    return int(match.group(1)), match.group(2)


def resolve_phase_file(repo: str | Path, number: int) -> Path | None:
    """Find the configured work-item file with ``number`` in its name."""
    phases = phases_folder(repo)
    if phases is None:
        return None
    for path in phase_paths(phases):
        parts = phase_file_parts(path)
        if parts and parts[0] == int(number):
            return path
    return None


def resolve_issue_file(repo: str | Path, number: int) -> Path | None:
    """Find the frozen task file the issue source wrote for ``number``.

    Issue tasks live in ``issues.task-dir``, never in the phases folder.
    """
    folder = setting("issues.task-dir", None)
    if folder is None:
        return None
    base = Path(folder)
    if not base.is_absolute():
        base = Path(repo) / base
    prefix = f"issue-{int(number):06d}"
    for path in sorted(base.glob(f"{prefix}*.md")):
        if path.stem == prefix or path.stem.startswith(prefix + "-"):
            return path
    return None


def _pipeline(repo: str | Path) -> dict[str, Any] | None:
    configured = setting("pipeline", None)
    if configured is None:
        return None
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("pipeline configuration needs PyYAML") from exc
    path = Path(configured)
    if not path.is_absolute():
        path = Path(repo) / path
    try:
        pipeline = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read pipeline {path}: {exc}") from exc
    if not isinstance(pipeline, dict):
        raise ValueError("pipeline must be a mapping")
    return cast("dict[str, Any]", pipeline)


def _pipeline_inputs(
    repo: str | Path,
    number: int,
    phase_file: str | Path | None,
    pipeline: dict[str, Any],
) -> dict[str, object]:
    number_name = setting("number-input", "phase_number")
    file_name = setting("file-input", "phase_file")
    if not number_name or not file_name or number_name == file_name:
        raise ValueError("phase input names must be distinct and nonempty")
    if phase_file is None:
        phase_file = resolve_phase_file(repo, number)
    declared = pipeline.get("inputs", {})
    if not isinstance(declared, dict):
        raise ValueError("pipeline inputs must be a mapping")
    declared = cast("dict[str, Any]", declared)
    overrides: dict[str, str] = {number_name: f"{int(number):06d}"}
    if phase_file is not None:
        path = Path(phase_file)
        if path.is_absolute():
            with contextlib.suppress(ValueError):
                path = path.relative_to(Path(repo).resolve())
        overrides[file_name] = str(path)
    if number_name not in declared or file_name not in declared:
        raise ValueError("pipeline must declare the configured phase inputs")
    resolved: dict[str, object] = {}
    for name, spec in declared.items():
        fields = (
            cast("dict[str, Any]", spec)
            if isinstance(spec, dict)
            else {"default": spec}
        )
        kind = fields.get("type", "str")
        if kind not in {"str", "int"}:
            raise ValueError(f"input {name!r}: type must be str or int")
        value = overrides.get(name, fields.get("default"))
        if value is None:
            if fields.get("required"):
                raise ValueError(f"missing required input {name!r}")
            continue
        try:
            resolved[name] = int(str(value)) if kind == "int" else str(value)
        except ValueError as exc:
            raise ValueError(f"input {name!r} needs an integer") from exc
    return resolved


def pipeline_inputs(
    repo: str | Path, number: int, phase_file: str | Path | None
) -> dict[str, object]:
    """Resolve phase overrides and typed defaults like Glide's input resolver."""
    pipeline = _pipeline(repo)
    return _pipeline_inputs(repo, number, phase_file, cast("dict[str, Any]", pipeline))


def phase_run_dir(
    repo: str | Path, number: int, phase_file: str | Path | None = None
) -> Path:
    """Return the run folder named by the configured pipeline."""
    root = Path(repo).resolve()
    pipeline = _pipeline(root)
    if pipeline is None:
        return runs_dir(root) / f"phase-{int(number):06d}"
    template = pipeline.get("run_dir")
    if not isinstance(template, str) or not template:
        raise ValueError("pipeline run_dir must be a nonempty string")
    inputs = _pipeline_inputs(root, number, phase_file, pipeline)
    try:
        path = Path(template.format_map(inputs))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"cannot resolve pipeline run_dir: {exc}") from exc
    return path if path.is_absolute() else root / path
