"""AuthSession — the `session` table (FR-AU-17…19, KAN-196): one row per
login — the local revocation check (no live call to an identity provider)
(FR-AU-18). The token's `sid` names the row; the platform session guard
refuses a request whose row is revoked, idle beyond `idle_expires_at` or past
`absolute_expires_at` (Anto, 2026-10-17: two hours idle, absolute at the next
midnight Europe/Zurich). A user sees and revokes their own rows in My
account; a manager revokes a user's; deactivation revokes them all.
"""

import datetime as dt
import uuid

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class AuthSession(PrimaryKeyMixin, Base):
    __tablename__ = "session"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, index=True)
    # The dealership the session was minted in (the home at login; a switch
    # keeps the row and the token's tenant moves). Platform-owned, a real FK.
    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    device_summary: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    amr: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    idle_expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    absolute_expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def is_live(self, at: dt.datetime) -> bool:
        return self.revoked_at is None and self.idle_expires_at > at and self.absolute_expires_at > at
