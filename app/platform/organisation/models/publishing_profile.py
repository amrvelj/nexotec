"""publishing_profile (KAN-225; Dealer Administration PRD, Publishing profile,
FR-A-04, FR-A-05, FR-A-27, A-RULE-2, D-A-26).

Everything the outside world sees of a Betrieb — the Nexotec-hosted dealer
website first (D-A-26), AutoScout24 second — as editorial content. Never a
credential or an account number (A-RULE-2: those are
`integration_connection.config`), and never a second copy of a fact another
screen owns:

- the public trade name is `dealership.trade_name` (Business screen);
- the website URL the documents print is `dealership.website` (Business);
- a site's lead e-mail is `location.lead_email` (Locations); the rest of its
  lead routing is `location_lead_routing` (this ticket);
- the logo for listings is the `primary` `branding_asset` (Branding).

One row per Betrieb (`location_id` NULL) and at most one override row per
location (FR-A-04, inherit-unless-set): an override row's NULL column — or a
language missing from a localised column — inherits the Betrieb's value.
Legal notices, the hosted site's domain, the published switch and the social
profiles belong to the legal entity and exist on the Betrieb row only (A-27:
the legal footer never changes per site); the service refuses them on an
override.

Localised columns hold `{"de": …, "fr": …, "it": …, "en": …}` — only the
languages written, never an empty string (the schema drops those).

The financing example's wording is pending legal review (A-6): the fields
exist so the dealer can enter the default example; the screen says so, and
nothing publishes it until A-6 is answered.

Both ends are platform-owned, so the foreign keys are real.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TimestampMixin, VersionedMixin
from app.core.types import GUID, UTCDateTime
from app.db import Base

PROFILE_LANGUAGES = ("de", "fr", "it", "en")

# Localised editorial texts a location may override.
OVERRIDABLE_LOCALISED_FIELDS = (
    "short_description",
    "long_description",
    "warranty_text",
    "mfk_statement",
    "price_display_wording",
    "standard_equipment_blurb",
    "trade_in_promotion_text",
)
# Scalars a location may override (listing defaults and the financing example).
OVERRIDABLE_SCALAR_FIELDS = (
    "delivery_fee_chf",
    "financing_term_months",
    "financing_down_payment_chf",
    "financing_residual_value_chf",
    "financing_nominal_rate_pct",
    "financing_effective_rate_pct",
    "financing_provider",
)
# The legal entity's own: the Betrieb row only.
BETRIEB_ONLY_LOCALISED_FIELDS = ("impressum", "privacy_notice", "withdrawal_wording")
BETRIEB_ONLY_SCALAR_FIELDS = ("website_domain", "is_published", "social_profiles", "agb_url")

SOCIAL_NETWORKS = ("facebook", "instagram", "linkedin", "youtube", "tiktok")


class PublishingProfile(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "publishing_profile"
    __table_args__ = (
        Index(
            "uq_publishing_profile_dealership",
            "dealership_id",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
            sqlite_where=text("location_id IS NULL"),
        ),
        Index(
            "uq_publishing_profile_location",
            "location_id",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
            sqlite_where=text("location_id IS NOT NULL"),
        ),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    # NULL — the Betrieb's profile; set — that site's override row.
    location_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("location.id"), nullable=True)

    # --- Public identity (the trade name is dealership.trade_name) -----------
    short_description: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    long_description: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    # The domain the Nexotec-hosted website answers on (FR-A-27) and its
    # published / unpublished switch. Betrieb row only.
    website_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # {network: url} for SOCIAL_NETWORKS. Betrieb row only.
    social_profiles: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # --- Legal notices (Betrieb row only) -------------------------------------
    impressum: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    privacy_notice: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    withdrawal_wording: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    # AGB: a hosted URL, or an uploaded PDF in the object store (the row keeps
    # the key and what was judged on upload, never the bytes) — or both.
    agb_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agb_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agb_byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agb_uploaded_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # --- Listing defaults ----------------------------------------------------
    warranty_text: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    mfk_statement: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    # Gross CHF incl. MwSt (ADR-057: one price).
    delivery_fee_chf: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    # The wording next to a listed price («inkl. MwSt»). One wording: every
    # vehicle is regelbesteuert (ADR-057 supersedes ADR-033's margin display).
    price_display_wording: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    standard_equipment_blurb: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    trade_in_promotion_text: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # --- Financing example (wording pending legal review, A-6) ---------------
    financing_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    financing_down_payment_chf: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    financing_residual_value_chf: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    financing_nominal_rate_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 3), nullable=True)
    financing_effective_rate_pct: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 3), nullable=True)
    financing_provider: Mapped[str | None] = mapped_column(String(200), nullable=True)


class LocationLeadRouting(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    """Per location (PRD Lead routing): the lead phone, whether an enquiry
    also creates a task in the DMS, and which user or role receives it. The
    lead e-mail is `location.lead_email` — one writer, the Locations
    master data; this screen writes it through the location service."""

    __tablename__ = "location_lead_routing"
    __table_args__ = (UniqueConstraint("location_id", name="uq_location_lead_routing_location_id"),)

    location_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("location.id"), nullable=False)
    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False, index=True)
    lead_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creates_task: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Exactly one of the two, or neither (then the site's managers receive it).
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    recipient_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
