"""PR-7's fleet-wide catalogue sync status — one row per (tenant,
provider), feeding the platform view's own health-board grid.
"""

import datetime as dt
import uuid

from app.core.schemas import CamelModel


class CatalogueSyncStatusRead(CamelModel):
    tenant_id: uuid.UUID
    provider_code: str
    last_full_seed_at: dt.datetime | None
    last_delta_cursor: dt.date | None
    last_system_watermark_date: dt.date | None
    last_system_checked_at: dt.datetime | None
    # Derived on read (never stored, never repaired by a nightly job —
    # the same posture every other derived-status field in this codebase
    # already uses), the same >7-day A-12 boundary catalogue_sync.py's
    # own alarm uses.
    stale: bool
