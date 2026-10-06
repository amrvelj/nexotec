"""Contracts Stock knows to be cancelled (KAN-158).

Delivery is at-least-once and not in order: a contract's
`sales.contract.cancelled` can reach Stock before its
`sales.contract.confirmed` has been handled (a failed confirmation is
retried after backoff, the cancellation is not held back behind it). Stock
records every cancellation it consumes here, so the confirmation consumer
never creates a manual configuration's pipeline item reserved for a
contract that is already cancelled. Stock's own record of a Sales fact,
written only by Stock's consumer — no cross-context read, no shared table.
"""

import datetime as dt
import uuid

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class InventoryCancelledContract(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "inventory_cancelled_contract"
    __table_args__ = (UniqueConstraint("tenant_id", "contract_id", name="uq_inventory_cancelled_contract_tenant_id_contract_id"),)

    contract_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the sales context (SalesContract.id). No DB-level FK."
    )
    # When Sales cancelled it (the event's occurred_at), not when Stock heard.
    cancelled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
