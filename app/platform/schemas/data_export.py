"""Data export and offboarding — the API shapes (KAN-218)."""

import datetime as dt
import uuid

from pydantic import Field

from app.core.schemas import CamelModel
from app.platform.models.export_job import ExportJobKind, ExportJobStatus
from app.platform.organisation.models.dealership import DealershipStatus


class ExportRead(CamelModel):
    id: uuid.UUID
    dealership_id: uuid.UUID
    kind: ExportJobKind
    status: ExportJobStatus
    requested_by: uuid.UUID
    reason: str | None
    created_at: dt.datetime
    produced_at: dt.datetime | None
    byte_size: int | None
    download_expires_at: dt.datetime | None
    acknowledged_at: dt.datetime | None
    acknowledged_by: uuid.UUID | None
    expired_at: dt.datetime | None
    failure_reason: str | None
    version: int


class ExportPage(CamelModel):
    items: list[ExportRead]
    next_cursor: str | None = None


class ArchiveExportRequest(CamelModel):
    """Q2 B: staff produce an archive export for a former manager who asked
    support — with the reason, which the audit row keeps."""

    reason: str = Field(min_length=3, max_length=500)


class OffboardRequest(CamelModel):
    confirmation: bool = Field(description="Must be true: offboarding deactivates every user and cannot be undone.")
    reason: str | None = Field(default=None, max_length=500)


class OffboardingRead(CamelModel):
    """Where a Betrieb stands on its way out (the platform offboarding page)."""

    dealership_id: uuid.UUID
    legal_name: str
    status: DealershipStatus
    version: int
    offboarded_at: dt.datetime | None
    retention_until: dt.date | None
    last_write_at: dt.datetime | None
    acknowledged_export_id: uuid.UUID | None
    acknowledged_export_produced_at: dt.datetime | None
    # None when offboarding may go ahead; else the stable reason it is refused.
    refusal: str | None
