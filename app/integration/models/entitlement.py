"""IntegrationEntitlement (WP-6 PR-1) — what a connection is actually
permitted to do, per capability. `source` distinguishes a value the
gateway itself observed by probing the provider (`probed`, PR-2's
`/test` action) from one a human declared in the UI ahead of a probe
(`declared`) — PR-5's degradation logic reads this table, never a
provider account's raw permission bits directly.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class EntitlementSource(str, enum.Enum):
    PROBED = "probed"
    DECLARED = "declared"


class IntegrationEntitlement(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_entitlement"
    __table_args__ = (
        UniqueConstraint("connection_id", "capability_code", name="uq_integration_entitlement_connection_capability"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_connection.id"), nullable=False, index=True
    )
    capability_code: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[EntitlementSource] = mapped_column(
        SAEnum(EntitlementSource, native_enum=False, length=16), nullable=False
    )
    checked_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
