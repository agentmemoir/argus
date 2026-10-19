# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""A minimal raw configuration for issue-source loader boundary tests."""

import copy
from typing import Any

from config_fixture import BASE


def issue_document() -> dict[str, Any]:
    document = copy.deepcopy(BASE)
    document["driver"]["provider"] = "issues"
    document["board"] = {
        "repo": ".",
        "file": "coordination/phases.json",
        "issues-file": "coordination/issues.json",
        "branch": "claims",
        "remote": "origin",
    }
    document["issues"] = {
        "repo": "example/project",
        "label-groups": {
            "type": {"labels": ["bug", "enhancement"], "cardinality": "one"},
            "difficulty": {
                "labels": ["difficulty:easy", "difficulty:hard"],
                "cardinality": "one",
            },
            "area": {"labels": ["area:cli", "area:store"], "cardinality": "many"},
        },
        "difficulty-order": ["difficulty:easy", "difficulty:hard"],
        "preference": "newest",
        "task-dir": "project/runs/issue-tasks",
    }
    return document
