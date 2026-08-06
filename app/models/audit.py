import datetime as dt

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import PrimaryKeyMixin, utcnow
from app.models.types import GUID, UTCDateTime


class AuditEvent(PrimaryKeyMixin, Base):
    """Append-only audit trail (spec cross-cutting #7). Rows are never
    updated or deleted after insert.
    """

    __tablename__ = "audit_event"

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[GUID] = mapped_column(GUID(), nullable=False, index=True)
    tenant_id: Mapped[GUID | None] = mapped_column(GUID(), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[GUID | None] = mapped_column(GUID(), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
