# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Failures raised while reading and validating configuration."""


class ConfigError(Exception):
    """Any problem with a configuration file or one of its values."""
