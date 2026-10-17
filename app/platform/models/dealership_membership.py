"""DealershipMembership: which OTHER dealerships (beyond a user's home
User.tenant_id) a user may switch their active dealership to (WP-3 PR-3).

A user's home dealership is never a row here — it's always a member of
`memberships` implicitly (see app.core.auth.create_access_token). This
table only records ADDITIONAL grants, e.g. a user who works across two
dealerships in the same group. Both User and Dealership are platform-owned,
so — unlike the cross-context GUID+comment convention — real DB foreign
keys are correct here.

is_dealer_manager (KAN-98, D-A-01): the manager flag is held per
dealership. This row's flag is the user's manager flag in THIS dealership
only; their home dealership's flag stays on User.is_dealer_manager. See
app.platform.services.user.is_dealer_manager_in.

granted_by / valid_to (KAN-199): who granted the access, and when it ended.
A removed sister-Betrieb access keeps its row with `valid_to` set, never
deleted (Anto, 2026-10-17); a re-grant reopens the row. Everything that
counts memberships — the manager guard, switch-dealership, manager e-mails —
counts only rows active now (`is_active`). `granted_by` is an actor column
under another name and is covered by the nightly actor reference check.
"""

import datetime as dt
import uuid

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class DealershipMembership(PrimaryKeyMixin, Base):
    __tablename__ = "dealership_membership"
    __table_args__ = (
        UniqueConstraint("user_id", "dealership_id", name="uq_dealership_membership_user_id_dealership_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    dealership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealership.id"), nullable=False, index=True
    )
    granted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    is_dealer_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    def is_active(self, at: dt.datetime) -> bool:
        return self.valid_to is None or self.valid_to > at
