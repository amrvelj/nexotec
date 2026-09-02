"""IntegrationCallPayload (WP-6 PR-6) — ADR-024's tier-2/tier-3 table: the
ONE place a raw provider payload ever exists, structurally separate from
`IntegrationCallLog` (see that model's own docstring). `EncryptedString`
at rest, same mechanism as `Dealership.tax_id`. Purge tiers (services/
retention.py): error payloads survive 30 days, successful payloads only
7 — a successful call is far less likely to ever need re-inspection than
one that failed, so it earns a much shorter retention window.

No dealer-facing endpoint ever returns a row from this table — the only
read path is the platform_admin-only break-glass endpoint (api/
call_payloads.py), itself audit-logged and notifying the dealer's own
manager (ADR-024/025) every time it's used.

`tenant_id` is nullable, denormalized, matching `IntegrationCallLog`'s
own reasoning exactly (a platform-scoped connection's calls have no
tenant) — never `TenantScopedMixin`, which would force a NOT NULL that
doesn't hold for every row this table could ever carry.
"""

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin
from app.core.types import GUID, EncryptedString
from app.db import Base


class PayloadKind(str, enum.Enum):
    SUCCESS = "success"
    ERROR = "error"


class IntegrationCallPayload(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_call_payload"
    __table_args__ = (UniqueConstraint("call_log_id", name="uq_integration_call_payload_call_log_id"),)

    call_log_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_call_log.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    kind: Mapped[PayloadKind] = mapped_column(SAEnum(PayloadKind, native_enum=False, length=16), nullable=False)
    payload: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
