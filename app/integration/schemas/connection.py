"""IntegrationConnection schemas (WP-6 PR-1). Write-only at the secret
boundary: `SecretWriteRequest` accepts a value; nothing in this module
can ever produce a response shape that echoes one back — there is no
`SecretRead`, deliberately.
"""

import datetime as dt
import uuid

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
    # KAN-222 (FR-A-21): one of the Betrieb's own locations, or null to unbind.
    # Left out = unchanged; only a marketplace or calendar_mail connection may
    # be bound (422 `location_binding_not_supported`).
    location_id: uuid.UUID | None = None


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
    # KAN-222 (FR-A-20): who the account is at the provider, derived from the
    # connection's non-secret configuration by its provider
    # (`services/account_identifier.py`); null when the configuration lacks it
    # — the card says «Kennung fehlt». Never derived from a secret.
    account_identifier: str | None
    # KAN-222 (FR-A-21): the location the connection serves, rule 2's copy of
    # its name, and whether the provider's accounts are per site at all.
    location_id: uuid.UUID | None
    location_label: str | None
    location_bindable: bool
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


class UsageCounterRead(CamelModel):
    """One of the provider's own counters, named verbatim (FR-A-23)."""

    counter: str
    count: int


class UsageRead(CamelModel):
    """KAN-222 (FR-A-23) — the connection's requests in a period, per
    provider counter. Counts only, never a CHF amount (Anto, 2026-11-14,
    Q1 A: prices are in each Betrieb's own contract). Indicative only
    (I-2/I-3): Nexotec never bills on top of a provider's own contract.
    `periodFrom`/`periodTo` are calendar days in Swiss time, both inclusive.
    """

    period_from: dt.date
    period_to: dt.date
    total_calls: int
    counters: list[UsageCounterRead]
    indicative: bool = True


class CapabilityCheckRead(CamelModel):
    """WP-6 PR-5 — the generic "can this tenant do X right now" read any
    screen may call, gated by nothing beyond authentication (unlike
    `integration_connections`, this reveals no connection detail, no
    secret, nothing manager-only)."""

    capability_code: str
    granted: bool
