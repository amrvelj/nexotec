import datetime as dt
import uuid

from sqlalchemy import JSON, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class AuditEvent(PrimaryKeyMixin, Base):
    """Append-only audit trail (spec cross-cutting #7). Rows are never
    updated or deleted after insert.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        # KAN-231: the per-user plate-read limit counts this user's recent
        # plate reads on every plate read. Partial, so it covers only those
        # rows and costs nothing on every other audit write.
        Index(
            "ix_audit_event_plate_read_actor_created",
            "actor_id",
            "created_at",
            postgresql_where=text("entity_type = 'vehicle_plate_history'"),
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
