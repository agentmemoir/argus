# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read, clean, resolve, and validate one configuration file."""

import copy
from pathlib import Path
from typing import Any, cast

from argus.config.document import read_document
from argus.config.document import strip_comments as strip_comments
from argus.config.errors import ConfigError
from argus.config.issue_validation import (
    validate_issues,
    validate_presence,
    validate_trust,
)
from argus.config.overlay import apply_overlay
from argus.config.phases_validation import validate_phases
from argus.config.schema import (
    _OBJECT_KEYS,
    _OVERRIDES,
    _REPORT_KEY,
    ISSUES_DEFAULTS,
    SCHEMA,
    Field,
)


class Config:
    """A loaded configuration: resolved values and where each one came from."""

    def __init__(
        self, path: Path, values: dict[str, object], sources: dict[str, str]
    ) -> None:
        self.path = path
        self.values = values
        self.sources = sources

    def get(self, key: str) -> object | None:
        return self.values.get(key)

    def source(self, key: str) -> str | None:
        return self.sources.get(key)


def load(
    config_path: str | Path | None = None,
    overrides: dict[str, object] | None = None,
) -> Config:
    """Read, clean, resolve, and validate one configuration file.

    `config_path=None` uses `./config.json` when it is a file. `overrides` may
    hold the four flag names `repo-root`, `phases-directory`, `phase-glob`, and
    `driver`; each present one wins over the file and its source is `flag`. A
    relative flag path resolves against the working directory.
    """
    if config_path is None:
        path = Path("config.json")
        if not path.is_file():
            raise ConfigError(
                f"no configuration file: pass --config, or create '{path.resolve()}'"
            )
        path = path.resolve()
    else:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigError(f"cannot read config file '{path}': not a file")
        path = path.resolve()

    raw = read_document(path)

    supplied_overrides: dict[str, object] = overrides or {}
    raw, origins = apply_overlay(path, raw)
    return _build(path, raw, supplied_overrides, origins)


def _build(
    path: Path,
    raw: dict[str, Any],
    overrides: dict[str, object],
    origins: dict[str, Path],
) -> Config:
    for legacy in ("roadmap-dir", "phases-dir"):
        if legacy in raw:
            raise ConfigError(
                f"key '{legacy}' was replaced by the 'phases' object: set "
                "'phases.directory' to the folder holding the work-item files, "
                "and 'phases.repo', 'phases.branch', 'phases.remote' with it"
            )

    for name in _OBJECT_KEYS:
        if name in raw and raw[name] is not None and not isinstance(raw[name], dict):
            raise ConfigError(f"invalid type for key '{name}': expected object")

    board_value = raw.get("board")
    board = (
        cast("dict[str, Any]", board_value) if isinstance(board_value, dict) else None
    )
    phases_value = raw.get("phases")
    phases = (
        cast("dict[str, Any]", phases_value) if isinstance(phases_value, dict) else None
    )
    notify_value = raw.get("notify")
    notify = (
        cast("dict[str, Any]", notify_value) if isinstance(notify_value, dict) else None
    )

    repair_value = raw.get("repair")
    repair = (
        cast("dict[str, Any]", repair_value) if isinstance(repair_value, dict) else None
    )
    issue_value = raw.get("issues")
    issues = (
        cast("dict[str, Any]", issue_value) if isinstance(issue_value, dict) else None
    )
    provider = overrides.get("driver")
    if provider is None:
        provider = _lookup(raw, "driver.provider")[1]
    validate_presence(provider, issues, board)

    supplied: dict[str, tuple[object, str]] = {}
    for field in SCHEMA:
        found, value = _lookup(raw, field.key)
        if found and value is not None:
            supplied[field.key] = (value, "overlay" if field.key in origins else "file")
    if provider == "issues":
        for key, default in ISSUES_DEFAULTS.items():
            supplied.setdefault(key, (copy.deepcopy(default), "default"))

    for flag, value in overrides.items():
        if value is None:
            continue
        if flag not in _OVERRIDES:
            raise ConfigError(f"unknown override '{flag}'")
        key, wrap = _OVERRIDES[flag]
        if wrap and not isinstance(value, list):
            value = [value]
        supplied[key] = (value, "flag")

    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    for field in SCHEMA:
        required = _is_required(field, board, notify, issues, repair, provider)
        if field.key in supplied:
            raw_value, source = supplied[field.key]
            _check_type(field, raw_value)
            _check_value(field, raw_value, required)
            config_dir = (
                Path.cwd() if source == "flag" else origins.get(field.key, path).parent
            )
            value = _convert(field, raw_value, config_dir)
            key = _REPORT_KEY.get(field.key, field.key)
            values[key] = value
            sources[key] = source
        elif required:
            raise ConfigError(f"missing required key '{field.key}'")
    if issues is not None:
        validate_trust(issues)
    if provider == "issues":
        validate_issues(values)
    if phases is not None:
        validate_phases(values)
    return Config(path, values, sources)


def _is_required(
    field: Field,
    board: dict[str, Any] | None,
    notify: dict[str, Any] | None,
    issues: dict[str, Any] | None,
    repair: dict[str, Any] | None,
    provider: object,
) -> bool:
    if field.required == "required":
        return True
    if field.required == "board":
        return board is not None
    if field.required == "phases":
        return provider in (None, "phase", "phases")
    if field.required == "notify":
        return notify is not None
    if field.required == "issues":
        return issues is not None and provider == "issues"
    if field.required == "repair":
        return repair is not None
    return False


def _lookup(raw: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    node: Any = raw
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return False, None
        mapping = cast("dict[str, Any]", node)
        if part not in mapping:
            return False, None
        node = mapping[part]
    return True, node


def _check_type(field: Field, value: object) -> None:
    kind = field.type
    if kind == "str":
        ok = isinstance(value, str)
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind in ("path", "id-file"):
        ok = isinstance(value, str)
    elif kind == "object":
        ok = isinstance(value, dict)
    elif kind == "str-list":
        ok = isinstance(value, list) and all(
            isinstance(item, str) for item in cast("list[object]", value)
        )
    else:
        ok = False
    if not ok:
        raise ConfigError(f"invalid type for key '{field.key}': expected {kind}")


def _convert(field: Field, value: object, config_dir: Path) -> object:
    if field.type not in ("path", "id-file"):
        return value
    candidate = Path(cast("str", value))
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    candidate = candidate.resolve()
    if field.type == "id-file":
        return _read_machine_id(candidate)
    return candidate


def _read_machine_id(path: Path) -> str:
    """Read a machine's name from the first non-empty line of ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read machine id file '{path}': {exc.strerror or exc}"
        ) from exc
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    raise ConfigError(f"machine id file '{path}' is empty")


def _check_value(field: Field, value: object, required: bool) -> None:
    if required and field.type == "str-list" and not value:
        raise ConfigError(
            f"invalid value for key '{field.key}': list must not be empty"
        )
    if required and field.type in ("str", "path") and value == "":
        raise ConfigError(f"missing required key '{field.key}'")
    if field.key == "repair.attempts" and cast("int", value) < 1:
        raise ConfigError("invalid value for key 'repair.attempts': must be at least 1")
    if field.key == "repair.wait" and cast("int", value) < 0:
        raise ConfigError("invalid value for key 'repair.wait': must not be negative")
    if field.key == "driver.state" and "/" in cast("str", value):
        raise ConfigError(
            "invalid value for key 'driver.state': must be a folder name, not a path"
        )
