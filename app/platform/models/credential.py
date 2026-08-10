"""Credential: the interim internal login's password store — deliberately a
separate table from User, so User's business record (spec cross-cutting
#10: "MDM never stores credentials") stays true even while this exists.
Throwaway by design: replaced, not extended, once a real external IdP
(Auth0/Entra/Okta) becomes a sales requirement for multi-dealer SSO.
"""

import datetime as dt

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime


class Credential(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "credential"

    # One credential set per User — enforced via a unique constraint, not
    # used as the PK, so PrimaryKeyMixin's UUIDv7 id stays consistent with
    # every other table.
    user_id: Mapped[GUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Basic brute-force guard (issue #8) — account-level lockout, not a
    # distributed/IP-based rate limiter (out of scope for the interim login).
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
