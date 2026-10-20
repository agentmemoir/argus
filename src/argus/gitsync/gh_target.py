# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The ``[HOST/]OWNER/REPO`` argument gh wants, on its own."""


def _repo_target(owner: str | None, repo: str | None, host: str | None) -> str:
    """The ``[HOST/]OWNER/REPO`` argument gh wants for one pull request.

    The host is passed through rather than matched, so a GitHub Enterprise
    install works the same way github.com does. Only the literal ``github.com``
    is dropped, because it is gh's own default.
    """
    target = f"{owner}/{repo}"
    if host and host not in ("github.com",):
        return f"{host}/{target}"
    return target
