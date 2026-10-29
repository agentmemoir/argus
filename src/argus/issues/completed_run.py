# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Whether an issue whose run completed has an Argus pull request."""

from argus.gitsync.issue_paths import issue_branch
from argus.issues.client import IssueClient
from argus.issues.pulls import history


def pull_request_verdict(number: int, task_name: str) -> str:
    """``pull_request`` when an Argus pull request exists for the issue.

    The issue branch and any suffixed branch count, in every state: open, closed,
    or merged. ``none`` means no pull request exists. A failed read raises.
    """
    client = IssueClient()
    pulls = client.pull_requests_on(issue_branch(number, task_name))
    return "pull_request" if pulls or history(client).get(number) else "none"
