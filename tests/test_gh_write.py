# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The create's signature: a body is required, so no empty one reaches gh.

``gh_create_draft_pull_request`` defaulted ``body`` to ``None`` and rendered
``""``, so a later caller could quietly reintroduce the empty-body draft the
mode refuses. The signature carries the requirement now, and this test keeps it
there: the call omits ``body``, so Python refuses it before gh is asked.
"""

from pathlib import Path
from typing import Any

import pytest

from argus.gitsync import gh_write
from gh_mode_support import BRANCH, STUB_OPEN_PR, Workspace, build_workspace


@pytest.fixture
def workspace(
    tmp_path: Path, exec_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Workspace:
    return build_workspace(
        tmp_path, exec_dir, monkeypatch, stub=STUB_OPEN_PR, defaults={}, branch=BRANCH
    )


def test_a_create_without_a_body_is_a_type_error(workspace: Workspace) -> None:
    # The call is deliberately one argument short, which pyright, in strict mode
    # over the suite, would reject, so it goes through a name.
    create: Any = gh_write.gh_create_draft_pull_request

    with pytest.raises(TypeError) as refused:
        create(workspace.root, BRANCH, "main")

    assert "body" in str(refused.value)
    assert workspace.commands() == []
