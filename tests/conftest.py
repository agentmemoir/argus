# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures.

A test that runs a stub CLI writes an executable file and puts it on PATH. Some
hosts mount their temporary storage `noexec`, where the file mode is correct and
the kernel refuses, so that stub must live in a directory the host really runs:
`exec_dir` gives one, probed by `argus.exec_tempdir`, and removes it with the
test. Do not write an executable into `tmp_path`; that is the failure this
fixture exists to remove.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from argus.exec_tempdir import exec_tempdir


@pytest.fixture
def exec_dir() -> Iterator[Path]:
    """A directory the host will run a stub from, removed with the test."""
    with exec_tempdir(purpose="an executable test stub") as directory:
        yield Path(directory)


@pytest.fixture
def body_file(tmp_path: Path) -> Path:
    """A body file with prose in it: `--body-file` is required for `--mode open-pr`."""
    path = tmp_path / "body.md"
    path.write_text("The pull request prose.\n", encoding="utf-8")
    return path
