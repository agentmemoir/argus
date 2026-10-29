# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Whether an issue whose run completed has an Argus pull request."""

from argus.gitsync.issue_paths import issue_branch
from argus.issues.client import IssueClient


def pull_request_verdict(number: int, task_name: str) -> str:
    """``pull_request`` when an Argus pull request exists on the issue branch.

    The branch is read in every state, so an open, closed, or merged pull request
    counts. ``none`` means no pull request exists. A failed read raises.
    """
    pulls = IssueClient().pull_requests_on(issue_branch(number, task_name))
    return "pull_request" if pulls else "none"
