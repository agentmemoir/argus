# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Argus owns selection, reservations, publication, and notifications.

Project pipelines, stages, and hooks stay outside this package. Direct source
commands register this package through _bootstrap, so the source checkout can
have any name. Installed packaging is a separate step.
"""
