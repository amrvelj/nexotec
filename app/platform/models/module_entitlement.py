"""module_entitlement (KAN-198, D-A-04, D-A-10).

Which modules a Betrieb is licensed for — `sales`, `aftersales`, `parts`,
`finance`, `reporting` — written only by platform_admin, read by the manager
("what we bought"), and carried as a JWT claim so every request is gated
server-side before any role check (app.core.permissions.require_entitlement).
One row per (dealership, module), re-used across grants (the audit log keeps
the earlier periods): a withdrawn
entitlement keeps its row with `valid_to` set, a re-grant reopens it. Both
sides are platform-owned, so a real FK to dealership is fine. `granted_by`
is the platform_admin user's id, NULL for the rows the KAN-198 migration
backfilled (every existing Betrieb got all five so nothing disappeared).
"""

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.permissions import ModuleCode
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ModuleEntitlement(PrimaryKeyMixin, Base):
    __tablename__ = "module_entitlement"
    __table_args__ = (UniqueConstraint("dealership_id", "module", name="uq_module_entitlement_dealership_id_module"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    module: Mapped[ModuleCode] = mapped_column(StoredEnum(ModuleCode, length=16), nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    valid_from: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    def is_active(self, at: dt.datetime) -> bool:
        return self.valid_from <= at and (self.valid_to is None or self.valid_to > at)
