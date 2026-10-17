"""Credential: the staff realm's password store (KAN-196, D-A-12; Authentication
& Identity, Credentials). A separate table from User on purpose — User is the
business record, this row is secret material — and the only table that may
hold a password-shaped column (tests/architecture/test_no_password_credential_storage.py,
inverted by D-A-07: the guard now asserts the column exists HERE and nowhere
else). A customer credential (D-A-13, reserved) can only ever be a second
table, never new columns here.

`password_hash` is an argon2id encoded string, parameters included, so the
policy can be raised without invalidating anyone (rehash on the next login).
`password_history` keeps the last five hashes to refuse immediate reuse on
reset (FR-AU-04: no rotation, so five is plenty). Lockout lives here, never
on User (FR-AU-09): editing the user record in the admin panel cannot clear
it; a successful reset does. Nothing here is ever logged, returned or
compared with `==` — argon2's constant-time verify only.
"""

import datetime as dt
import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class Credential(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "credential"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("user.id"), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_history: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    password_set_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    # FR-AU-05: the breach check could not be made when this password was
    # set (the service was unreachable) — the set succeeded and is flagged.
    breach_check_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
