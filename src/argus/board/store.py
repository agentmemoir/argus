# Copyright 2026 Agent Memoir (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The reservation store, composed from its role-specific parts."""

from argus.board.store_claim import _ClaimOps
from argus.board.store_group import _GroupOps
from argus.board.store_lifecycle import _LifecycleOps
from argus.board.store_publication import _PublicationOps
from argus.board.store_takeover import _TakeoverOps


class ReservationStore(
    _ClaimOps,
    _GroupOps,
    _LifecycleOps,
    _TakeoverOps,
    _PublicationOps,
):
    """Read and mutate the shared reservation state for one repository."""
