# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Read, clean, resolve, and validate one configuration file."""

import json
from pathlib import Path

from argus.config.schema import _OBJECT_KEYS, _OVERRIDES, _REPORT_KEY, SCHEMA


class ConfigError(Exception):
    """Any problem with the configuration file or one of its values."""


def strip_comments(text):
    """Remove `//` and `/* */` comments, keeping string literals intact.

    The scanner follows RFC 8259, section 7 for escapes: inside a string a
    reverse solidus escapes the character after it, so an escaped quote does
    not close the string. Newlines inside a block comment are kept so a parse
    error still points at roughly the original line.
    """
    out = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            out.append(char)
            index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            index += 2
            while index < length and text[index] != "\n":
                index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            closed = False
            while index < length:
                if text[index] == "\n":
                    out.append("\n")
                    index += 1
                elif (
                    text[index] == "*" and index + 1 < length and text[index + 1] == "/"
                ):
                    index += 2
                    closed = True
                    break
                else:
                    index += 1
            if not closed:
                raise ConfigError("unterminated block comment")
        else:
            out.append(char)
            index += 1
    return "".join(out)


class Config:
    """A loaded configuration: resolved values and where each one came from."""

    def __init__(self, path, values, sources):
        self.path = path
        self.values = values
        self.sources = sources

    def get(self, key):
        return self.values.get(key)

    def source(self, key):
        return self.sources.get(key)


def load(config_path=None, overrides=None):
    """Read, clean, resolve, and validate one configuration file.

    `config_path=None` uses `./config.json` when it is a file. `overrides` may
    hold the four flag names `repo-root`, `roadmap-dir`, `phase-glob`, and
    `driver`; each present one wins over the file and its source is `flag`.
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

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read config file '{path}': {exc.strerror or exc}"
        ) from exc

    try:
        cleaned = strip_comments(text)
    except ConfigError as exc:
        raise ConfigError(f"cannot parse config file '{path}': {exc}") from exc

    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"cannot parse config file '{path}' at line {exc.lineno} "
            f"column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config file '{path}' must hold a JSON object")

    return _build(path, raw, overrides or {})


def _build(path, raw, overrides):
    for name in _OBJECT_KEYS:
        if name in raw and raw[name] is not None and not isinstance(raw[name], dict):
            raise ConfigError(f"invalid type for key '{name}': expected object")

    board = raw.get("board")
    board = board if isinstance(board, dict) else None
    notify = raw.get("notify")
    notify = notify if isinstance(notify, dict) else None

    supplied = {}
    for field in SCHEMA:
        found, value = _lookup(raw, field.key)
        if found and value is not None:
            supplied[field.key] = (value, "file")

    for flag, value in overrides.items():
        if value is None:
            continue
        if flag not in _OVERRIDES:
            raise ConfigError(f"unknown override '{flag}'")
        key, wrap = _OVERRIDES[flag]
        if wrap and not isinstance(value, list):
            value = [value]
        supplied[key] = (value, "flag")

    values = {}
    sources = {}
    for field in SCHEMA:
        required = _is_required(field, board, notify)
        if field.key in supplied:
            raw_value, source = supplied[field.key]
            _check_type(field, raw_value)
            value = _convert(field, raw_value, path.parent)
            _check_value(field, value, required)
            key = _REPORT_KEY.get(field.key, field.key)
            values[key] = value
            sources[key] = source
        elif required:
            raise ConfigError(f"missing required key '{field.key}'")
    return Config(path, values, sources)


def _is_required(field, board, notify):
    if field.required == "required":
        return True
    if field.required == "board":
        return board is not None
    if field.required == "notify":
        return notify is not None
    return False


def _lookup(raw, dotted):
    node = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _check_type(field, value):
    kind = field.type
    if kind == "str":
        ok = isinstance(value, str)
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind in ("path", "id-file"):
        ok = isinstance(value, str)
    elif kind == "str-list":
        ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
    else:
        ok = False
    if not ok:
        raise ConfigError(f"invalid type for key '{field.key}': expected {kind}")


def _convert(field, value, config_dir):
    if field.type not in ("path", "id-file"):
        return value
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    candidate = candidate.resolve()
    if field.type == "id-file":
        return _read_machine_id(candidate)
    return candidate


def _read_machine_id(path):
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


def _check_value(field, value, required):
    if required and field.type == "str-list" and not value:
        raise ConfigError(
            f"invalid value for key '{field.key}': list must not be empty"
        )
    if required and field.type in ("str", "path") and value == "":
        raise ConfigError(f"missing required key '{field.key}'")
    if field.key == "driver.state" and "/" in value:
        raise ConfigError(
            "invalid value for key 'driver.state': must be a folder name, not a path"
        )
