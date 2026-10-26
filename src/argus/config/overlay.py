# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply one machine settings file without changing base-value provenance."""

from pathlib import Path
from typing import Any, cast

from argus.config.document import read_document
from argus.config.errors import ConfigError
from argus.config.schema import SCHEMA


def apply_overlay(
    path: Path, raw: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Merge top-level objects one level; replace scalars and lists wholesale."""
    machine = raw.get("machine")
    if not isinstance(machine, dict):
        return raw, {}
    name = cast("dict[str, Any]", machine).get("settings-file")
    if name is None:
        return raw, {}
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(
            "invalid value for key 'machine.settings-file': expected path"
        )
    overlay_path = (path.parent / name).resolve()
    overlay = read_document(overlay_path)
    merged = dict(raw)
    origins: dict[str, Path] = {}
    for section, value in overlay.items():
        if section not in raw:
            raise ConfigError(f"overlay key '{section}' is absent from the base file")
        if isinstance(value, dict):
            base = raw[section]
            if not isinstance(base, dict):
                raise ConfigError(
                    f"overlay key '{section}' cannot merge into an object"
                )
            value = cast("dict[str, Any]", value)
            base = cast("dict[str, Any]", base)
            for key in value:
                dotted = f"{section}.{key}"
                if dotted == "machine.settings-file":
                    raise ConfigError("overlay may not name 'machine.settings-file'")
                if key not in base:
                    raise ConfigError(
                        f"overlay key '{dotted}' is absent from the base file"
                    )
                origins[dotted] = overlay_path
            merged[section] = {**base, **value}
        else:
            merged[section] = value
            for field in SCHEMA:
                if field.key == section or field.key.startswith(section + "."):
                    origins[field.key] = overlay_path
    return merged, origins
