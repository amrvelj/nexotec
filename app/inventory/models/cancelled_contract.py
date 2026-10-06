"""Contracts Stock knows to be cancelled (KAN-158).

Delivery is at-least-once and not in order: a contract's
`sales.contract.cancelled` can reach Stock before its
`sales.contract.confirmed` has been handled (a failed confirmation is
retried after backoff, the cancellation is not held back behind it). Stock
records every cancellation it consumes here, so the confirmation consumer
never creates a manual configuration's pipeline item reserved for a
contract that is already cancelled. Stock's own record of a Sales fact,
written only by Stock's consumer — no cross-context read, no shared table.

Both consumers take the same per-contract advisory lock
(services/reservation.py::lock_contract) before they read or write here, so
a confirmation and a cancellation handled at the same time by two workers
are serialised rather than interleaved.
"""

import datetime as dt
import uuid

from sqlalchemy import String, UniqueConstraint
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
    # Rule 2's display label (the contract number, from the event) and when
    # it was read.
    contract_label: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_denorm_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    # When Sales cancelled it (the event's occurred_at), not when Stock heard.
    cancelled_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
