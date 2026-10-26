# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words for a gh exit code, and the step that was running."""

# The two gh exit codes its own documentation names, and the only two that need
# words of their own. Every other code is reported as the number alone.
GH_AUTH_FAILED = 4
GH_CHECKS_PENDING = 8


def gh_exit_detail(code: int, step: str) -> str:
    """Fixed words for a gh exit code, naming the step that was running.

    ``step`` names the gh call, such as ``the list``, ``the create``, or
    ``the checks read``. The words are short and stable so a log reader can
    tell a missing credential (exit 4) from a protected branch (exit 1)
    without seeing gh's message. The message is never reported: an
    unauthenticated call prints the host account name into it, and run logs are
    copied into issues.
    """
    if code == 0:
        return f"gh ran {step} and exited 0"
    if code == 1:
        return f"gh ran {step} and failed (gh exited 1)"
    if code == GH_AUTH_FAILED:
        return f"gh could not authenticate (gh exited 4) for {step}"
    if code == GH_CHECKS_PENDING:
        return f"gh reported checks pending (gh exited 8) during {step}"
    return f"gh exited {code}"
