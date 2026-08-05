import datetime as dt
import uuid
from typing import Any

from app.schemas.base import CamelModel


class AuditEventRead(CamelModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    tenant_id: uuid.UUID | None
    action: str
    actor_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None
    created_at: dt.datetime


class AuditEventPage(CamelModel):
    items: list[AuditEventRead]
    next_cursor: str | None
