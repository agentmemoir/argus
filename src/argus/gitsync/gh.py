# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The GitHub CLI primitives, re-exported from the module that owns each one.

Reads live in ``gh_read``, writes in ``gh_write``, the check rows in
``gh_checks``, and the shared ``[HOST/]OWNER/REPO`` target in ``gh_target``.
This module stays the import path every caller already uses.
"""

from argus.gitsync.gh_checks import (
    gh_pull_request_checks as gh_pull_request_checks,
)
from argus.gitsync.gh_read import (
    gh_pull_request_body as gh_pull_request_body,
)
from argus.gitsync.gh_read import (
    gh_pull_request_open as gh_pull_request_open,
)
from argus.gitsync.gh_read import (
    gh_pull_request_state as gh_pull_request_state,
)
from argus.gitsync.gh_read import (
    gh_pull_requests_for_branch as gh_pull_requests_for_branch,
)
from argus.gitsync.gh_target import (
    repo_target as repo_target,
)
from argus.gitsync.gh_write import (
    gh_comment_pull_request as gh_comment_pull_request,
)
from argus.gitsync.gh_write import (
    gh_create_draft_pull_request as gh_create_draft_pull_request,
)
from argus.gitsync.gh_write import (
    gh_edit_pull_request_body as gh_edit_pull_request_body,
)
from argus.gitsync.gh_write import (
    gh_mark_pull_request_ready as gh_mark_pull_request_ready,
)

__all__ = [
    "gh_comment_pull_request",
    "gh_create_draft_pull_request",
    "gh_edit_pull_request_body",
    "gh_mark_pull_request_ready",
    "gh_pull_request_body",
    "gh_pull_request_checks",
    "gh_pull_request_open",
    "gh_pull_request_state",
    "gh_pull_requests_for_branch",
    "repo_target",
]
