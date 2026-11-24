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

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, text
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


class GbpPushState(PrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """KAN-226 PR-3: what Nexotec last pushed to one binding's Google location
    (one row per binding, written by the push consumer only).

    `source_hash` is the SHA-256 of the location's pushed facts as the last
    successful push read them: an event whose facts hash the same sends
    nothing (the per-location debounce — a burst of facts is one push of the
    current state). `unmapped_departments` names the departments whose hours
    Google's category offers no hours type for (not sent; PR-4's drift card
    names them)."""

    __tablename__ = "integration_gbp_push_state"
    __table_args__ = (UniqueConstraint("binding_id", name="uq_integration_gbp_push_state_binding"),)

    binding_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_gbp_location_binding.id"), nullable=False
    )
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pushed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pushed_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    unmapped_departments: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class GbpDriftOutcome(str, enum.Enum):
    # Google shows what Nexotec pushes, field by field.
    IN_SYNC = "in_sync"
    # At least one pushed field differs at Google (`drifted_fields`).
    DRIFT = "drift"
    # Google could not be read (`reason` names why); nothing is known.
    FAILED = "failed"
    # Not compared (`reason`): the location is deactivated (ADR-199).
    SKIPPED = "skipped"


class GbpDriftCheck(PrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """KAN-226 PR-4: the latest nightly drift check of one binding (one row
    per binding, replaced by each check; written by the drift job only).

    The job reads the binding's Google location back and compares it, field
    by field, with what the push would send now (`gbp_push.build_patch`).
    It never repairs: the row is what the drift card and the status page
    read. `drifted_fields` holds the field codes of `gbp_drift.FIELD_CODES`
    (`address`, `phone`, …), never a value from Google."""

    __tablename__ = "integration_gbp_drift_check"
    __table_args__ = (UniqueConstraint("binding_id", name="uq_integration_gbp_drift_check_binding"),)

    binding_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_gbp_location_binding.id"), nullable=False
    )
    checked_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    outcome: Mapped[GbpDriftOutcome] = mapped_column(StoredEnum(GbpDriftOutcome, length=16), nullable=False)
    drifted_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # A closed code for `failed` / `skipped` (`provider_unavailable`, `location_inactive`, …).
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GbpPhotoJob(PrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """KAN-226 / KAN-784 (ADR-209): one binding waiting for a photo pass —
    the queue between the worker, which learns of a change, and the web
    service, which alone reads the object store's disk (ADR-081 as amended).

    The worker's consumers upsert the row (`due_at` now, the attempt count
    cleared, `generation` moved on); the web service's `gbp_photo_runner`
    claims a due row by moving `due_at` a lease ahead, uploads, and deletes
    the row once the pass is done — unless a consumer queued it again
    meanwhile (another `generation`), which leaves it due. A failure backs
    off (`attempts`, `due_at`); after the last attempt `dead_at` is set: an ERROR log line and a status-page warning (rule 10)
    until the next change queues it afresh."""

    __tablename__ = "integration_gbp_photo_job"
    __table_args__ = (UniqueConstraint("binding_id", name="uq_integration_gbp_photo_job_binding"),)

    binding_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_gbp_location_binding.id"), nullable=False
    )
    due_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Moved on by every queuing: a pass settles only the generation it claimed.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dead_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # A closed code naming the last failure (`provider_unavailable`, …), never a provider's text.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GbpPhotoUpload(PrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """KAN-226 / KAN-784 (ADR-209): one photo Nexotec uploaded to one
    binding's Google location, under one category — or that Google refused
    (`refusal_code`, e.g. a size outside Google's photo guidelines; the
    status page names it and it is not sent again) — what makes each upload
    happen once. Never deleted with the photo: Nexotec never deletes at
    Google (Anto, 2026-11-21), so the row outlives an image removed in
    Nexotec, as the photo outlives it at Google. A hero that becomes the
    primary one is uploaded again, as the new `COVER`; the earlier cover
    stays at Google, as ruled."""

    __tablename__ = "integration_gbp_photo_upload"
    __table_args__ = (
        UniqueConstraint("binding_id", "asset_id", "category", name="uq_integration_gbp_photo_upload_asset"),
        # Exactly one outcome: Google's media item, or Google's refusal.
        CheckConstraint(
            "(google_media_name IS NULL) <> (refusal_code IS NULL)",
            name="ck_integration_gbp_photo_upload_outcome",
        ),
    )

    binding_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("integration_gbp_location_binding.id"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), nullable=False, comment="Owned by the platform context (BrandingAsset.id). No DB-level FK."
    )
    # Google's v4 media category (`COVER`, `EXTERIOR`, `TEAMS`, `ADDITIONAL`).
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    # Google's media item, as Google named it (`accounts/…/locations/…/media/…`); null when refused.
    google_media_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # A closed code when Google refused this image (`provider_refused_request`); never Google's text.
    refusal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When it was uploaded, or refused.
    uploaded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
