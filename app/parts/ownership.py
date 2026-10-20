"""Parts' ownership provider (KAN-200; FR-A-37, D-A-35) — a stub, like the
context. Purchase orders assigned to a buyer will be the group once Parts
ships; until then the provider answers nothing.
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.ownership import OwnedGroup, OwnershipTransfer

MODULE = "parts"


class PartsOwnershipProvider:
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
        raise ValueError(f"parts owns no group '{transfer.group}' yet")
