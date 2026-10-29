"""IntegrationCallLog (WP-6 PR-1) — the ADR-024 tier-1 table: call
metadata, retained 24 months, backing quota enforcement and cost
attribution ("why is my auto-i-dat bill high?", I-2/I-3). Deliberately
carries NO payload column, ever — the one place a raw payload exists is
`IntegrationCallPayload` (PR-6), a wholly separate table with its own
7-day/30-day tiers and platform_admin-only break-glass access. Keeping
them structurally apart means no future careless serializer change on
THIS table could ever leak a payload into the 24-month-retained metadata.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class CallStatus(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class IntegrationCallLog(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_call_log"

    # KAN-470 (Anto, 2026-10-26, option A): the call log outlives its
    # connection until ADR-024 retention purges it, so this is a plain id,
    # not a foreign key, with the connection's name copied beside it at the
    # time of the call (the three-column pattern inside the context). The
    # label is never refreshed on rename: it says which account a call was
    # made on, as it was called then.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
        comment="IntegrationConnection.id. No DB-level FK: the row outlives a deleted connection (KAN-470).",
    )
    connection_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    connection_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Denormalized (not TenantScopedMixin — a platform-scoped connection's
    # calls have no tenant) so PR-6's per-tenant retention purge and PR-7's
    # per-tenant usage aggregation never need a join back through
    # integration_connection just to filter by tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CallStatus] = mapped_column(StoredEnum(CallStatus, length=16), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_units: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 4), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
