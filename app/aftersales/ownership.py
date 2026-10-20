"""Aftersales' ownership provider (KAN-200; FR-A-37, D-A-35) — a stub, like
the context. Service orders, appointments and assigned jobs will be the
groups once Aftersales ships; until then the provider answers nothing, so
the deactivation step lists the module from the registry and never has to
remember to add it later.
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.ownership import OwnedGroup, OwnershipTransfer

MODULE = "aftersales"


class AftersalesOwnershipProvider:
    module = MODULE

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        return []

    def refuses(self, db: Session, *, from_user_id: uuid.UUID, transfer: OwnershipTransfer) -> str | None:
        return None

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
        reason: str | None,
    ) -> int:
        raise ValueError(f"aftersales owns no group '{transfer.group}' yet")
