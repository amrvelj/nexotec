"""IntegrationConnection schemas (WP-6 PR-1). Write-only at the secret
boundary: `SecretWriteRequest` accepts a value; nothing in this module
can ever produce a response shape that echoes one back — there is no
`SecretRead`, deliberately.
"""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, ConnectionStatus
from app.integration.models.secret_ref import SecretSlot


class ConnectionCreate(CamelModel):
    provider_id: uuid.UUID
    display_name: str
    environment: ConnectionEnvironment
    config: dict = Field(default_factory=dict)
    # None on a dealer-authored connection (tenant comes from the token,
    # cross-cutting #6) — only a platform_admin request may set scope to
    # platform, and the service layer enforces that, never trusting scope
    # from a dealer-manager caller's own request body.
    scope: ConnectionScope = ConnectionScope.TENANT


class ConnectionUpdate(CamelModel):
    display_name: str | None = None
    config: dict | None = None
    expires_at: dt.datetime | None = None


class EntitlementRead(CamelModel):
    capability_code: str
    granted: bool
    source: str
    checked_at: dt.datetime


class SecretSlotRead(CamelModel):
    """The slot's own metadata only — never `secretRef`, never a value."""

    slot: SecretSlot
    rotated_at: dt.datetime | None


class ConnectionRead(CamelModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    provider_code: str
    scope: ConnectionScope
    tenant_id: uuid.UUID | None
    display_name: str
    environment: ConnectionEnvironment
    config: dict
    enabled: bool
    status: ConnectionStatus
    last_verified_at: dt.datetime | None
    last_error: str | None
    expires_at: dt.datetime | None
    rotated_at: dt.datetime | None
    secret_slots: list[SecretSlotRead] = Field(default_factory=list)
    entitlements: list[EntitlementRead] = Field(default_factory=list)
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ConnectionPage(CamelModel):
    items: list[ConnectionRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool


class SecretWriteRequest(CamelModel):
    # Named `secret_value`, not the bare `value` a first draft might reach
    # for — this exact field name is registered in
    # app.core.redact.SECRET_FIELDS so the logging boundary redacts it if
    # it ever lands in an `extra={}` log line; "value" is too generic a
    # name to blanket-redact everywhere without catching unrelated fields.
    secret_value: str


class DeleteConnectionRequest(CamelModel):
    confirm: bool = False


class UsageRead(CamelModel):
    """PR-7 — indicative only (I-2/I-3): Nexotec never bills on top of a
    provider's own contract, so this is never a billing artifact.
    """

    calls_this_period: int
    cost_units_this_period: Decimal | None
    indicative: bool = True
