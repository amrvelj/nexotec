"""The plate-lookup cache (FR-C-02, C-D / KAN-42).

`KontrollschildInfo` is billed per call and advisors retype the same plate
while they talk to a customer, so every answer is kept for
`PLATE_LOOKUP_CACHE_TTL` (`app.vehicle.services.plate_lookup_cache`) and a
repeat lookup inside that window costs nothing.

**Tenant-partitioned** (ADR-013): the rows are licensed provider data,
fetched with this dealer's own credentials, so a sister tenant never reads
them. The PRD's "improves with every lookup any dealer performs" would make
this table global; ADR-013 wins and the cache stays per tenant.

**Never enumerable** (PRD-Configurator §Data protection, inherited risk R-2:
a plate plus a Stammnummer is arguably personal data once it is linked to a
keeper). Rows are read by an exact plate or an exact Stammnummer only —
`tests/architecture/test_plate_lookup_is_not_enumerable.py` covers the
service functions that read this table, and no route lists it.

One row per record `KontrollschildInfo` returned: a Wechselschild is two
rows under one plate. A refetch replaces every row for the plate.
"""

import datetime as dt

from sqlalchemy import Date, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin
from app.core.types import UTCDateTime
from app.db import Base


class PlateLookupCacheEntry(PrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "vehicle_plate_lookup_cache"
    __table_args__ = (
        Index("ix_vehicle_plate_lookup_cache_tenant_plate", "tenant_id", "plate"),
        Index("ix_vehicle_plate_lookup_cache_tenant_stammnummer", "tenant_id", "stammnummer"),
    )

    # Normalised: canton letters + digits, no whitespace ("ZH123456").
    plate: Mapped[str] = mapped_column(String(16), nullable=False)
    vehicle_kind_code: Mapped[str] = mapped_column(String(8), nullable=False)
    brand_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_description: Mapped[str] = mapped_column(String(200), nullable=False)
    production_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    production_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type_approval_number: Mapped[str] = mapped_column(String(16), nullable=False)
    first_registration_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    stammnummer: Mapped[str] = mapped_column(String(16), nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
