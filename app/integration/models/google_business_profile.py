"""The Google Business Profile channel (KAN-226 PR-2; Dealer Administration
D-A-32, FR-A-35): one Nexotec location bound to one Google Business Profile
location, pushed one way (Nexotec → Google) by later PRs.

The shape is KAN-215's mailbox, not a connection per location (ADR in the
Target Architecture, KAN-226 PR-2): Nexotec's own OAuth client at Google is the
one platform-scope connection to `google_business_profile` (config `clientId`,
slot `client_secret`), every request goes through it, and a Betrieb's binding is
a row of its own here:

- `GbpLinkState` — one pending link (the OAuth round trip). Only the SHA-256 of
  the single-use `state` is stored, bound to the user, the Betrieb and the
  location being connected; it lives ten minutes. The PKCE verifier is derived
  from the state (HKDF, `services/gbp_link.py`), never stored.
- `GbpLocationBinding` — one Nexotec location's link to one Google location.
  `pending` from the callback until the manager picks the Google location,
  then `active`; `error` when Google refuses the grant (the manager
  reconnects); `disconnected` once ended. Its refresh token lives in the
  secrets manager under the binding's id, slot `refresh_token`, and nowhere
  else: the reference is derived from the id (`secrets_backend.secret_ref_for`),
  so no column holds one.

Platform's ids (`tenant_id` = Dealership.id, the user, the location) are plain
GUIDs with an owner comment, never a foreign key (rule 2); the location's name
is copied beside its id (the three-column pattern), refreshed nightly by
`integration.location_labels`.
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base

GBP_PROVIDER_CODE = "google_business_profile"


class GbpBindingStatus(str, enum.Enum):
    # Google granted access; the manager has not picked the Google location yet.
    PENDING = "pending"
    ACTIVE = "active"
    # Google refused the grant (`invalid_grant`, a 401/403): the manager reconnects.
    ERROR = "error"
    DISCONNECTED = "disconnected"


class GbpLinkState(PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_gbp_link_state"

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Dealership.id). No DB-level FK."
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class GbpLocationBinding(PrimaryKeyMixin, VersionedMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "integration_gbp_location_binding"
    __table_args__ = (
        # One live binding per Nexotec location.
        Index(
            "uq_integration_gbp_binding_location_live",
            "location_id",
            unique=True,
            postgresql_where=text("status <> 'disconnected'"),
            sqlite_where=text("status <> 'disconnected'"),
        ),
        # One live binding per Google location, across every Betrieb.
        Index(
            "uq_integration_gbp_binding_google_location_live",
            "google_location_name",
            unique=True,
            postgresql_where=text("status <> 'disconnected' AND google_location_name IS NOT NULL"),
            sqlite_where=text("status <> 'disconnected' AND google_location_name IS NOT NULL"),
        ),
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, index=True, comment="Owned by the platform context (Location.id). No DB-level FK."
    )
    location_label: Mapped[str] = mapped_column(String(200), nullable=False)
    location_label_refreshed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[GbpBindingStatus] = mapped_column(
        StoredEnum(GbpBindingStatus, length=16), nullable=False, default=GbpBindingStatus.PENDING
    )
    # Google's resource names (`accounts/…`, `locations/…`) and the location's
    # title at Google, as Google answered them when the manager picked it.
    google_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_location_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # A closed code when `status` is error (`grant_refused`).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    disconnected_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
