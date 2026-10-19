"""Inventory's ownership provider for the deactivation step (KAN-200;
FR-A-37, D-A-35). Stock items, pipeline vehicles and reservations belong
to the Betrieb, not to a person: no inventory record carries a user as
its owner (the ledger's `created_by` is authorship, kept by A-RULE-5). The
provider registers so the deactivation can say "inventory: nothing" from
the registry rather than from an omission, and answers an empty list.
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.ownership import OwnedGroup, OwnershipTransfer

MODULE = "inventory"


class InventoryOwnershipProvider:
    module = MODULE

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        return []

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
    ) -> int:
        raise ValueError(f"inventory owns no group '{transfer.group}'")
