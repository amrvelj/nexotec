import datetime as dt

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.types import GUID, UTCDateTime
from app.core.uuid7 import uuid7


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class PrimaryKeyMixin:
    """UUIDv7 primary key, server-side-equivalent generation (cross-cutting #2)."""

    id: Mapped[GUID] = mapped_column(GUID(), primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )
    created_by: Mapped[GUID | None] = mapped_column(GUID(), nullable=True)
    updated_by: Mapped[GUID | None] = mapped_column(GUID(), nullable=True)


class VersionedMixin:
    """Optimistic-concurrency column. Paired with the If-Match dependency in
    app.core.concurrency — every mutating endpoint on a versioned entity must
    check the caller's If-Match header against this value before writing.
    """

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class TenantScopedMixin:
    """Marks a model as tenant-owned. tenant_id is always the owning Dealer.id
    (cross-cutting #6: resolved from the JWT claim, never a path/body param).
    """

    tenant_id: Mapped[GUID] = mapped_column(GUID(), nullable=False, index=True)
