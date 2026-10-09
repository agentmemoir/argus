# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared settings, paths, and phase names for Argus tools."""

import contextlib
import re
from pathlib import Path

_SETTINGS: dict[str, object] = {}
_MISSING = object()


def configure(settings):
    """Replace the settings used by the current tool invocation."""
    global _SETTINGS
    _SETTINGS = dict(settings)
    return _SETTINGS


def override(settings):
    """Apply explicit settings to the current invocation."""
    previous = _SETTINGS
    configure({**_SETTINGS, **settings})
    return previous


def settings_from_config(config):
    """Return the validated values loaded by ``argus.config``."""
    return dict(config.values)


def configure_from_file(config_path=None, overrides=None):
    """Load one config file and publish its values to shared settings."""
    from argus.config import load

    config = load(config_path, overrides)
    configure(settings_from_config(config))
    return config


def configure_from_args(args):
    """Load settings and the four shared CLI overrides from parsed arguments."""
    overrides = {}
    for key, name in (
        ("repo-root", "config_repo_root"),
        ("roadmap-dir", "config_roadmap_dir"),
        ("phase-glob", "config_phase_glob"),
        ("driver", "config_driver"),
    ):
        value = getattr(args, name, None)
        if value is not None:
            overrides[key] = value
    return configure_from_file(args.config, overrides)


def configure_from_cli(argv):
    """Load shared settings from CLI flags embedded in a helper invocation."""
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--repo-root", dest="config_repo_root")
    parser.add_argument("--roadmap-dir", dest="config_roadmap_dir")
    parser.add_argument("--phase-glob", dest="config_phase_glob", action="append")
    parser.add_argument("--driver", dest="config_driver")
    args, _unknown = parser.parse_known_args(argv)
    return configure_from_args(args)


def setting(key, default=_MISSING):
    """Read one configured value, with defaults only for optional fields."""
    if key in _SETTINGS:
        return _SETTINGS[key]
    if default is not _MISSING:
        return default
    raise ValueError(f"configuration key '{key}' is not loaded")


def board_repo(repo=None):
    """The configured checkout that owns the reservation board."""
    path = setting("board.repo", None)
    if path is None:
        return None
    path = Path(path)
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path.resolve()


def phase_globs():
    """The configured work-item filename patterns."""
    return list(setting("driver.include"))


def phase_excluded(name):
    """True when ``name`` matches a configured excluded file name."""
    return Path(name).name in set(setting("driver.exclude", []))


def phase_paths(folder):
    """Every configured work-item file, in stable order and without repeats."""
    found = set()
    for pattern in phase_globs():
        found.update(Path(folder).glob(pattern))
    return [path for path in sorted(found) if not phase_excluded(path.name)]


def runs_dir(repo=None):
    """The run-state folder named in the loaded config."""
    path = Path(setting("driver.runs"))
    if not path.is_absolute():
        path = Path(repo or setting("repo-root")) / path
    return path


def phase_file_parts(path):
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


def resolve_phase_file(repo, number):
    """Find the configured work-item file with ``number`` in its name."""
    roadmap = Path(setting("roadmap-dir"))
    if not roadmap.is_absolute():
        roadmap = Path(repo) / roadmap
    for path in phase_paths(roadmap):
        parts = phase_file_parts(path)
        if parts and parts[0] == int(number):
            return path
    return None


def _pipeline(repo):
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
    return pipeline


def _pipeline_inputs(repo, number, phase_file, pipeline):
    number_name = setting("number-input", "phase_number")
    file_name = setting("file-input", "phase_file")
    if not number_name or not file_name or number_name == file_name:
        raise ValueError("phase input names must be distinct and nonempty")
    if phase_file is None:
        phase_file = resolve_phase_file(repo, number)
    declared = pipeline.get("inputs", {})
    if not isinstance(declared, dict):
        raise ValueError("pipeline inputs must be a mapping")
    overrides = {number_name: f"{int(number):06d}"}
    if phase_file is not None:
        path = Path(phase_file)
        if path.is_absolute():
            with contextlib.suppress(ValueError):
                path = path.relative_to(Path(repo).resolve())
        overrides[file_name] = str(path)
    if number_name not in declared or file_name not in declared:
        raise ValueError("pipeline must declare the configured phase inputs")
    resolved = {}
    for name, spec in declared.items():
        fields = spec if isinstance(spec, dict) else {"default": spec}
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


def pipeline_inputs(repo, number, phase_file):
    """Resolve phase overrides and typed defaults like Glide's input resolver."""
    return _pipeline_inputs(repo, number, phase_file, _pipeline(repo))


def phase_run_dir(repo, number, phase_file=None):
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
