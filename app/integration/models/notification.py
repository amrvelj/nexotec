"""IntegrationNotification (WP-6 PR-6, ADR-025) — one row per warning
actually sent, the record that makes "fires only at exactly T-30/14/7"
enforceable: the daily job checks this table before sending, so a
connection whose `expires_at` sits at T-29 today never gets a duplicate
T-30 warning it already sent yesterday, and a job that runs twice in one
day (a restart mid-cycle) never double-sends either.

`kind` distinguishes an expiry warning (in-app + email to the dealer's
own manager, per tenant, at T-30/14/7) from a break-glass access
notification (also to the dealer's manager, one per access, no batching)
from the daily aggregated support digest (to Nexotec support, one row
per day regardless of how many warnings/alarms it bundles — "never
per-event" is enforced by there being exactly one row per (kind=digest,
sent_date), not one per underlying event).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Date, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID
from app.db import Base


class NotificationKind(str, enum.Enum):
    EXPIRY_WARNING = "expiry_warning"
    BREAK_GLASS_ACCESS = "break_glass_access"
    SUPPORT_DIGEST = "support_digest"


class IntegrationNotification(PrimaryKeyMixin, TimestampMixin, Base):
    """No DB-level uniqueness constraint: it would have to apply
    differently per `kind` (at most one `expiry_warning` per connection+
    threshold+day; a `break_glass_access` row per actual access, no
    dedup at all; exactly one `support_digest` per day). Deduplication is
    the caller's own query-then-insert check (services/notifications.py),
    the same "single worker, at-least-once, tolerate the theoretical
    race" posture this codebase already applies to `MappingGap`'s own
    upsert and `ProviderSyncState`'s get-or-create.
    """

    __tablename__ = "integration_notification"

    # Nullable: the support digest has no single connection — it
    # aggregates across every tenant's warnings for the day.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    kind: Mapped[NotificationKind] = mapped_column(SAEnum(NotificationKind, native_enum=False, length=24), nullable=False)
    # Which T-N threshold this is, for an expiry warning; null for the
    # other two kinds, which have no threshold concept.
    threshold_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
