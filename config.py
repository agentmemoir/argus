# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""One configuration file for an Argus tick, and the CLI that renders it.

The file is JSON with `//` and `/* */` comments. Comments are removed with a
small scanner that respects JSON string literals and backslash escapes, so a
`//` inside a string stays part of the value. The escape rules follow RFC 8259,
section 7: a reverse solidus escapes the next character, so `\\"` does not end
the string and `\\\\` leaves the scanner ready to end it.

Every relative path is resolved against the folder that holds the file. A flag
beats the file for one run, and `sources` records which one supplied each value.
No value has a default: a missing required key stops the load and names the key.
`machine.id-file` names a file whose first non-empty line is the machine's name,
so machines with the same layout can share one configuration file.
"""

import argparse
import collections
import json
import shlex
import sys
from pathlib import Path


class ConfigError(Exception):
    """Any problem with the configuration file or one of its values."""


Field = collections.namedtuple("Field", "key type required")

# The schema, in report order. `required` is one of:
#   "required" - always needed;
#   "optional" - never required;
#   "board"    - required only when the `board` object is present;
#   "notify"   - required only when the `notify` object is present.
SCHEMA = (
    Field("repo-root", "path", "required"),
    Field("pipeline", "path", "required"),
    Field("roadmap-dir", "path", "required"),
    Field("number-input", "str", "optional"),
    Field("file-input", "str", "optional"),
    Field("driver.provider", "str", "required"),
    Field("driver.include", "str-list", "required"),
    Field("driver.exclude", "str-list", "optional"),
    Field("driver.runs", "path", "required"),
    Field("driver.state", "str", "required"),
    Field("driver.stale-after", "int", "required"),
    Field("driver.outage-stale", "int", "required"),
    Field("driver.quiet-minutes", "int", "required"),
    Field("driver.keep-logs-days", "int", "required"),
    Field("driver.merge-check-max", "int", "required"),
    Field("driver.recover-own-claim", "bool", "required"),
    Field("machine.id-file", "id-file", "required"),
    Field("commands.glide", "str", "required"),
    Field("commands.git", "str", "required"),
    Field("commands.tmux", "str", "required"),
    Field("commands.python", "str", "required"),
    Field("commands.hermes", "str", "required"),
    Field("commands.gh", "str", "required"),
    Field("tmux.session", "str", "required"),
    Field("tmux.width", "int", "required"),
    Field("tmux.height", "int", "required"),
    Field("tmux.idle-window", "str", "required"),
    Field("tmux.phase-window", "str", "required"),
    Field("tmux.idle-script", "str", "required"),
    Field("tmux.refresh", "int", "required"),
    Field("board.repo", "path", "board"),
    # A path inside the board repository, so it stays repository-relative.
    Field("board.file", "str", "board"),
    Field("board.branch", "str", "board"),
    Field("board.remote", "str", "board"),
    Field("notify.profile", "str", "notify"),
    Field("notify.channel", "str", "notify"),
    Field("notify.disabled", "bool", "notify"),
    Field("notify.fallback", "str", "optional"),
    Field("notify.heartbeat", "str", "optional"),
)

# Dotted key -> shell variable, in the same order as SCHEMA. The shell format
# prints one variable per present key, so board/notify stay unset when absent.
SHELL_VARS = {
    "repo-root": "REPO",
    "pipeline": "PIPELINE",
    "roadmap-dir": "ROADMAP_DIR",
    "number-input": "PHASE_NUMBER_INPUT",
    "file-input": "PHASE_FILE_INPUT",
    "driver.provider": "DRIVER",
    "driver.include": "PHASE_INCLUDE",
    "driver.exclude": "PHASE_EXCLUDE",
    "driver.runs": "WF",
    "driver.state": "DRIVER_STATE",
    "driver.stale-after": "STALE_AFTER_SEC",
    "driver.outage-stale": "OUTAGE_STALE_SEC",
    "driver.quiet-minutes": "QUIET_MINUTES",
    "driver.keep-logs-days": "LOG_KEEP_DAYS",
    "driver.merge-check-max": "MERGE_CHECK_MAX",
    "driver.recover-own-claim": "RECOVER_OWN_CLAIM",
    "machine.id": "PHASE_MACHINE_ID",
    "commands.glide": "CMD_GLIDE",
    "commands.git": "CMD_GIT",
    "commands.tmux": "CMD_TMUX",
    "commands.python": "CMD_PYTHON",
    "commands.hermes": "CMD_HERMES",
    "commands.gh": "CMD_GH",
    "tmux.session": "SESSION_NAME",
    "tmux.width": "TMUX_WIDTH",
    "tmux.height": "TMUX_HEIGHT",
    "tmux.idle-window": "TMUX_IDLE_WINDOW",
    "tmux.phase-window": "TMUX_PHASE_WINDOW",
    "tmux.idle-script": "TMUX_IDLE_SCRIPT",
    "tmux.refresh": "TMUX_REFRESH",
    "board.repo": "BOARD_REPO",
    "board.file": "BOARD_FILE",
    "board.branch": "BOARD_BRANCH",
    "board.remote": "BOARD_REMOTE",
    "notify.profile": "HERMES_PROFILE_NAME",
    "notify.channel": "DISCORD_CHANNEL",
    "notify.disabled": "NOTIFY_DISABLED",
    "notify.fallback": "DISCORD_FALLBACK_CHANNEL",
    "notify.heartbeat": "HEARTBEAT_URL",
}

# The four flag overrides: flag name -> (dotted key, wrap scalar in a list).
# Repeated --phase-glob flags replace the file's include list with that list.
_OVERRIDES = {
    "repo-root": ("repo-root", False),
    "roadmap-dir": ("roadmap-dir", False),
    "phase-glob": ("driver.include", True),
    "driver": ("driver.provider", False),
}

_OBJECT_KEYS = ("driver", "machine", "commands", "tmux", "board", "notify")

# Two keys name one value: the file a machine reads its own name from, and the
# name it reads. Only the name is stored, reported and passed to the shell.
_REPORT_KEY = {"machine.id-file": "machine.id"}


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
                elif (text[index] == "*" and index + 1 < length
                      and text[index + 1] == "/"):
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
                f"no configuration file: pass --config, or create "
                f"'{path.resolve()}'")
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
            f"cannot read config file '{path}': {exc.strerror or exc}") from exc

    try:
        cleaned = strip_comments(text)
    except ConfigError as exc:
        raise ConfigError(f"cannot parse config file '{path}': {exc}") from exc

    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"cannot parse config file '{path}' at line {exc.lineno} "
            f"column {exc.colno}: {exc.msg}") from exc

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
            f"cannot read machine id file '{path}': {exc.strerror or exc}") from exc
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    raise ConfigError(f"machine id file '{path}' is empty")


def _check_value(field, value, required):
    if required and field.type == "str-list" and not value:
        raise ConfigError(
            f"invalid value for key '{field.key}': list must not be empty")
    if required and field.type in ("str", "path") and value == "":
        raise ConfigError(f"missing required key '{field.key}'")
    if field.key == "driver.state" and "/" in value:
        raise ConfigError(
            "invalid value for key 'driver.state': must be a folder name, "
            "not a path")


def _shell_value(value):
    if isinstance(value, list):
        if not value:
            return "()"
        return "( " + " ".join(shlex.quote(str(item)) for item in value) + " )"
    if isinstance(value, bool):
        return "1" if value else "0"
    return shlex.quote(str(value))


def shell_lines(config):
    """One assignment per present variable, plus its source, in schema order."""
    lines = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        var = SHELL_VARS[key]
        lines.append(f"{var}={_shell_value(config.values[key])}")
        lines.append(f"ARGUS_SOURCE_{var}='{config.sources[key]}'")
    return lines


def _render(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def report_lines(config):
    """`key<TAB>source<TAB>value` for every present key, in schema order."""
    lines = []
    for field in SCHEMA:
        key = _REPORT_KEY.get(field.key, field.key)
        if key not in config.values:
            continue
        lines.append(
            f"{key}\t{config.sources[key]}\t"
            f"{_render(config.values[key])}")
    return lines


def _json_view(config):
    values = {}
    for key, value in config.values.items():
        values[key] = str(value) if isinstance(value, Path) else value
    return {"path": str(config.path), "values": values,
            "sources": dict(config.sources)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="argus.config",
        description="Load an Argus configuration and print it for a caller.")
    parser.add_argument("--config", help="config file; defaults to ./config.json")
    parser.add_argument("--repo-root")
    parser.add_argument("--roadmap-dir")
    parser.add_argument("--phase-glob", action="append")
    parser.add_argument("--driver")
    parser.add_argument("--format", choices=("shell", "json", "report"),
                        default="report")
    args = parser.parse_args(argv)

    overrides = {}
    if args.repo_root is not None:
        overrides["repo-root"] = args.repo_root
    if args.roadmap_dir is not None:
        overrides["roadmap-dir"] = args.roadmap_dir
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
