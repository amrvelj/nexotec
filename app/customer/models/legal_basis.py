"""LegalBasis: revDSG joint-controllership evidence per customer per group
(WP-3 PR-4, ADR-030). Sister dealerships within a group are separate legal
entities/controllers — sharing the group-owned Customer record across them
needs a joint-controller agreement, not just a technical group_id match.

Append-only, same pattern as AuditEvent: never UPDATE a grant row. A
withdrawal is a NEW row with withdrawn_at set at insert time, not a mutation
of the original grant — "versioned and dated, never a boolean" (the brief's
own words). Whether a customer currently has a LIVE basis is a query
("is there a row for (customer_id, group_id) with withdrawn_at IS NULL,
ordered by granted_at desc"), not a column read.
"""

import datetime as dt
import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class LegalBasis(PrimaryKeyMixin, Base):
    __tablename__ = "legal_basis"

    customer_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    # No DB-level FK to dealer_group.id — same comment convention as every
    # other cross-context reference (P-2). Owned by the platform context.
    group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (DealerGroup). No DB-level FK."
    )
    basis: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    withdrawn_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    source_document: Mapped[str] = mapped_column(Text, nullable=False)
    # Who recorded this row — accountability for a compliance record, not a
    # full TimestampMixin (granted_at already is this row's "created at";
    # an append-only row is never updated, so updated_by never changes
    # after insert, but is still worth capturing as "who else touched this
    # customer's compliance history").
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
