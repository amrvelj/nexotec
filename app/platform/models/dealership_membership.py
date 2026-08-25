"""DealershipMembership: which OTHER dealerships (beyond a user's home
User.tenant_id) a user may switch their active dealership to (WP-3 PR-3).

A user's home dealership is never a row here — it's always a member of
`memberships` implicitly (see app.core.auth.create_access_token). This
table only records ADDITIONAL grants, e.g. a user who works across two
dealerships in the same group. Both User and Dealership are platform-owned,
so — unlike the cross-context GUID+comment convention — real DB foreign
keys are correct here.
"""

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
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
