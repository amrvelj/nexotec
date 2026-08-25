import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, Index, Integer, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    DEAD = "dead"


class OutboxMessage(PrimaryKeyMixin, Base):
    """Transactional outbox (PR-4, ADR-006). One shared table in core, not
    one per context — same pattern as audit_event/idempotency_record/
    reconciliation_run, all core-owned tables every context writes to
    through a core-provided API, not a seam violation.

    `id` IS `eventId` — one identifier, not two. Written by app.core.outbox
    .publish() in the SAME session/transaction as the caller's business
    write; see that module's docstring for the session-boundary rules that
    make at-least-once delivery actually hold.
    """

    __tablename__ = "outbox_message"
    __table_args__ = (
        # The poller's only hot query: WHERE status='pending' AND
        # next_attempt_at <= now() ORDER BY id. The partial predicate
        # already filters status, so indexing status again inside would be
        # redundant — this indexes exactly what the query still has to
        # search on once status is fixed.
        # SQLAlchemy's Enum(native_enum=False) persists the member NAME
        # ('PENDING'), not the value ('pending') — confirmed empirically,
        # same convention as every other enum column in this codebase (see
        # the b7c1e4a92f10 migration's note on this exact gotcha).
        Index(
            "ix_outbox_message_pending_next_attempt_at",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the platform context (Dealership). No DB-level FK."
    )
    producer: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    # Generic JSON, not a Postgres-specific JSONB variant — matches the
    # portable-types convention (app/core/config.py, user_preference.py).
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    status: Mapped[OutboxStatus] = mapped_column(
        SAEnum(OutboxStatus, native_enum=False, length=16), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    published_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
