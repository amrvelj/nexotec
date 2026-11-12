"""Valuation's own record of which contracts carry a valuation as their
trade-in (KAN-631, ADR-131).

Valuation sits below Sales (ADR-131), so it may not ask Sales «is this
valuation carried by a pending or a signed contract?». It keeps the answer
itself, one row per (valuation, contract), written by the calls Sales already
makes into `valuation.public` (ADR-047 Pattern B: each on a session of its
own, with its own commit):

- `pending` — a pending contract carries the valuation. Written **before**
  the Sales write that makes the contract carry it commits (a contract from
  an offer, a trade-in valuation attached or auto-attached), and by the
  confirmation's consume, so «Verwendung aufheben» sees a carry at least as
  early as the contract exists (KAN-520).
- `signed` — a signed contract carries it. Written **after** the signing
  commits. It never goes back: a cancelled signed contract stays signed on
  its valuation (ADR-066).
- `released` — the contract no longer carries it: cancelled while pending,
  or its trade-in valuation replaced. Written after that Sales commit.

Every write lands on the restrictive side when it fails: a pending row
missed after the fact keeps the clear refused, never allows it. Nightly
reconciliation (`valuation/reconciliation.py`) reports a row that disagrees
with Sales; nothing repairs it from there.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ContractCarryState(str, enum.Enum):
    PENDING = "pending"
    SIGNED = "signed"
    RELEASED = "released"


class ValuationContractCarry(PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """One contract carrying one valuation as its trade-in. Tenant-private,
    as the valuation is (ADR-029): `tenant_id` is the valuation's, and a
    contract of another dealership is never recorded."""

    __tablename__ = "valuation_contract_carry"
    __table_args__ = (
        UniqueConstraint("valuation_id", "contract_id", name="uq_valuation_contract_carry_valuation_contract"),
    )

    valuation_id: Mapped[uuid.UUID] = mapped_column(
        # No index of its own: the unique constraint's leading column serves it.
        GUID(), ForeignKey("valuation.id", name="fk_valuation_contract_carry_valuation_id"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True,
        comment="Owned by the sales context (SalesContract.id). No DB-level FK.",
    )
    # Rule 2's label: the contract's number, a business key that never changes.
    contract_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contract_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    state: Mapped[ContractCarryState] = mapped_column(StoredEnum(ContractCarryState, length=16), nullable=False)
    signed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
