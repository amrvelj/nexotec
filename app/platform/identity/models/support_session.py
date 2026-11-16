"""A Nexotec support session (KAN-221 PR-2; Dealer Administration D-A-05,
D-A-24, FR-A-15, FR-A-17, FR-A-18).

A platform staff member steps into one user's work for a stated reason, for
at most 60 minutes. This row is the record: who, for whom, in which Betrieb,
why, and how it ended. The token that carries `acting_for` is PR 3 (the
preserved `app/core/auth.py`, Anto's go of 2026-11-14 for an attended
session); until it exists a session grants nothing.

**How it ended:** `ended_by` is `staff` or `user` when one of them ended it.
A session nobody ended is over at `expires_at`: reads derive `timeout` at once
(`services/support_sessions.py::state_of`), and the nightly
`platform.support_sessions.close_expired` — or the staff member's next start,
whichever comes first — writes that ending into the row, as a system write.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base

SUPPORT_SESSION_MINUTES = 60


class SupportSessionEndedBy(str, enum.Enum):
    """Who ended a session (FR-A-18). `timeout` is derived from `expires_at`
    until the nightly job or the staff member's next start writes it."""

    STAFF = "staff"
    USER = "user"
    TIMEOUT = "timeout"


class SupportSession(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_session"
    __table_args__ = (
        # One session a staff member has not ended at a time; an expired one
        # is closed (`timeout`) by the nightly job or by the next start.
        Index(
            "uq_support_session_staff_open",
            "staff_user_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    staff_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    target_user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    # The target user's home Betrieb: the tenant the session acts in.
    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_by: Mapped[SupportSessionEndedBy | None] = mapped_column(
        StoredEnum(SupportSessionEndedBy, length=16), nullable=True
    )
