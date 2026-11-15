"""What the status page keeps besides its derivation (KAN-223; Dealer
Administration D-A-38, FR-A-43).

The page itself is **derived** on every read (`services/status_page.py`):
health is never stored. What is kept here:

- `platform_status_incident` — Nexotec's own platform status: an incident
  platform staff write (title and text in the four UI languages, a severity,
  from, to). No external status-page service in v1. An incident is current
  while `starts_at <= now` and `ends_at` is unset or later; it reaches every
  Betrieb's page.
- `status_item_state` — the daily job's notification ledger: one row per
  Betrieb × item key holding the last state it saw and when the managers were
  told, so a degradation raises one `administration.status_item_degraded`
  notification, nothing more while it stays degraded, and a new one only
  after it was ok again. About who was told, never about health.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.status import StatusState
from app.core.types import GUID, UTCDateTime
from app.db import Base


class IncidentSeverity(str, enum.Enum):
    """How an incident shows on every Betrieb's page: `warning` (degraded —
    something is slow or partly unavailable) or `blocked` (an outage)."""

    WARNING = "warning"
    BLOCKED = "blocked"


class PlatformStatusIncident(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "platform_status_incident"
    __table_args__ = (Index("ix_platform_status_incident_starts_at", "starts_at"),)

    severity: Mapped[IncidentSeverity] = mapped_column(StoredEnum(IncidentSeverity, length=16), nullable=False)
    # {"de": …, "fr": …, "it": …, "en": …} — all four, never a fallback.
    title: Mapped[dict] = mapped_column(JSON, nullable=False)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, comment="The staff user (platform).")


class StatusItemState(PrimaryKeyMixin, Base):
    __tablename__ = "status_item_state"
    __table_args__ = (UniqueConstraint("dealership_id", "item_key", name="uq_status_item_state_dealership_id_item_key"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    item_key: Mapped[str] = mapped_column(String(96), nullable=False)
    # The source that contributed the item: while that source cannot be asked,
    # its rows are kept, so its recovery is not a new degradation.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[StatusState] = mapped_column(StoredEnum(StatusState, length=16), nullable=False)
    # When the state last changed (as the job saw it).
    changed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    # When the managers were last told about this item (None: never, or the
    # source tells them itself).
    notified_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
