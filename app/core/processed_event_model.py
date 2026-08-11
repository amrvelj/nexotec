import datetime as dt

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import utcnow
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ProcessedEvent(Base):
    """Consumer-side idempotency bookkeeping (PR-4, Decision 6). Composite
    PK (event_id, consumer_name) — not a plain event_id PK — because
    several consumers legitimately process the same event, and each one's
    delivery/redelivery status is tracked independently. See
    app.core.consumer.consume_once() for the one rule that makes this
    table meaningful: a consumer writes its row here in the SAME
    transaction as its side effect, never separately.

    No PrimaryKeyMixin: the PK here is the (event_id, consumer_name) pair
    itself, not a synthetic id — same shape as CustomerNumberSequence.
    """

    __tablename__ = "processed_event"

    event_id: Mapped[GUID] = mapped_column(GUID(), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    processed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
