"""IntegrationConnection (WP-6 PR-1) — one configured connection to a
provider, dealer-scoped or platform-scoped. Sandbox and production are
always two separate rows, never a flag on one row (Integrations & API
Credentials v0.1, rule 7) — the `environment` column is part of the
natural key precisely so a live tenant's production connection can never
be flipped to sandbox by accident.

`tenant_id` is nullable — most connections are `scope="tenant"`
(auto-i-dat is per-dealer, ADR-013), but a handful are `scope="platform"`
(a messaging/email-relay connection Nexotec itself holds). The check
constraint below ties the two together so the column can never silently
drift out of sync with its own scope.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base


class ConnectionScope(str, enum.Enum):
    PLATFORM = "platform"
    TENANT = "tenant"


class ConnectionEnvironment(str, enum.Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class ConnectionStatus(str, enum.Enum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"
    EXPIRED = "expired"
    DISABLED = "disabled"


class IntegrationConnection(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "integration_connection"
    __table_args__ = (
        CheckConstraint(
            # SAEnum(native_enum=False) stores the Python member NAME
            # (uppercase), not .value — the same convention every other
            # enum column in this codebase already relies on (no
            # values_callable anywhere in app/). 'PLATFORM'/'TENANT' here,
            # never the lowercase .value strings.
            "(scope = 'PLATFORM' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL)",
            name="ck_integration_connection_scope_tenant_id",
        ),
        # NULL is not equal to NULL in a SQL unique constraint, so this
        # does not by itself prevent two platform-scoped duplicates for
        # the same (provider, environment) — that case is rare, always
        # platform_admin-authored, and checked at the service layer
        # instead (services/connections.py::create_connection). It DOES
        # fully enforce the common, dealer-authored case: one tenant can
        # never hold two connections to the same provider in the same
        # environment.
        UniqueConstraint(
            "tenant_id", "provider_id", "environment", name="uq_integration_connection_tenant_provider_env"
        ),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_provider.id"), nullable=False, index=True
    )
    scope: Mapped[ConnectionScope] = mapped_column(
        SAEnum(ConnectionScope, native_enum=False, length=16), nullable=False
    )
    # No TenantScopedMixin: that mixin forces NOT NULL, which a
    # platform-scoped connection must not have (see the check constraint).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    environment: Mapped[ConnectionEnvironment] = mapped_column(
        SAEnum(ConnectionEnvironment, native_enum=False, length=16), nullable=False
    )
    # Non-secret only — endpoint URLs, customer/branch/mandant numbers,
    # and (A-9) an optional "retentionMode" key, default "full_cache" when
    # absent, so a future reversal to lazy caching is a config value, not
    # a rebuild.
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[ConnectionStatus] = mapped_column(
        SAEnum(ConnectionStatus, native_enum=False, length=16),
        nullable=False,
        default=ConnectionStatus.NOT_CONFIGURED,
    )
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A contract/certificate expiry the dealer manager counts down against
    # (ADR-025's T-30/14/7 warnings need something to count from — no
    # table in the original 5-table model carried one). Nullable: most
    # secret slots (a plain password, an AES key) don't expire on a timer.
    expires_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    rotated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
