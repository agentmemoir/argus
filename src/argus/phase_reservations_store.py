# Copyright 2026 Agent Memoir Developers (https://agentmemoir.com/)
# SPDX-License-Identifier: Apache-2.0
"""The reservation store, composed from its role-specific parts."""

from argus.phase_reservations_store_claim import _ClaimOps
from argus.phase_reservations_store_group import _GroupOps
from argus.phase_reservations_store_lifecycle import _LifecycleOps
from argus.phase_reservations_store_publication import _PublicationOps
from argus.phase_reservations_store_takeover import _TakeoverOps


class ReservationStore(
    _ClaimOps,
    _GroupOps,
    _LifecycleOps,
    _TakeoverOps,
    _PublicationOps,
):
    """Read and mutate the shared reservation state for one repository."""
