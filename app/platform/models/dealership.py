"""The three-level organisation model (WP-3, ADR-014): dealer_group ->
Dealership -> Location.

Dealership (renamed from Dealer, WP-3) is still the tenant. Dealership.id is
the tenant_id used everywhere else (spec cross-cutting #1, issue #2) — the
group does NOT become the tenant, and nothing about existing scoping changes.
Not TenantScopedMixin — a Dealership IS the tenant, it doesn't belong to one.

DealerGroup owns customers group-wide (ADR-014) once PR-2/PR-4 land, and
carries the single point of contact for data-subject requests (ADR-030).
Every existing dealership becomes a group of one at migration time — see
alembic/versions/platform/<this-migration>.

Location is owned by platform, not inventory: aftersales needs the workshop,
platform needs where a user works, finance needs the site on document
footers. calendar_ref is empty in v1 — retrofitting a working-calendar
reference after Aftersales ships is the expensive version, so the column
exists now rather than then.
"""

import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, JSON, Boolean, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.i18n import SwissLanguage
from app.core.types import GUID, EncryptedString
from app.db import Base


class FranchiseType(str, enum.Enum):
    FRANCHISE = "franchise"
    INDEPENDENT = "independent"


class DealershipStatus(str, enum.Enum):
    PENDING_ONBOARDING = "pending_onboarding"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


class DealerGroup(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "dealer_group"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # ADR-030 (4): dealer_group carries a single point of contact for data
    # subjects. Optional at creation — every existing group is backfilled
    # from a group-of-one dealership that never supplied one.
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ADR-030 (2): the group-read path is behind this flag, platform_admin-
    # only to flip, and only once a legal_basis row exists for the group at
    # all (app.platform.services.dealership.enable_group_read's own
    # precondition — a fail-fast admin UX check, NOT the actual security
    # boundary; the read helper re-checks a LIVE basis per customer on every
    # call regardless of this flag, since a basis can be withdrawn after the
    # flag was flipped ON and nothing else would catch that).
    group_read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Dealership(PrimaryKeyMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "dealership"

    # Same-context real FK (dealer_group and dealership are both
    # platform-owned) — replaces the unused parent_group_id scaffolding
    # column this model carried before dealer_group existed.
    dealer_group_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("dealer_group.id"), nullable=False, index=True
    )

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dba_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dealer_license_number: Mapped[str] = mapped_column(String(64), nullable=False)
    license_state: Mapped[str] = mapped_column(String(2), nullable=False)
    franchise_type: Mapped[FranchiseType] = mapped_column(
        SAEnum(FranchiseType, native_enum=False, length=32), nullable=False
    )
    oem_affiliations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Swiss address (addendum decision #2): flat columns, not a nested/JSON
    # blob, so it's queryable/validatable like any other required field.
    address_street: Mapped[str] = mapped_column(String(200), nullable=False)
    address_house_number: Mapped[str] = mapped_column(String(20), nullable=False)
    address_postal_code: Mapped[str] = mapped_column(String(4), nullable=False)
    address_locality: Mapped[str] = mapped_column(String(100), nullable=False)
    address_canton: Mapped[str] = mapped_column(String(2), nullable=False)
    address_country: Mapped[str] = mapped_column(String(2), nullable=False, default="CH")

    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    # Encrypted at rest (EncryptedString) per Swiss addendum tax_id
    # requirement — see key-management caveat on Settings.tax_id_encryption_key.
    tax_id: Mapped[str] = mapped_column(EncryptedString(), nullable=False)

    status: Mapped[DealershipStatus] = mapped_column(
        SAEnum(DealershipStatus, native_enum=False, length=32),
        nullable=False,
        default=DealershipStatus.PENDING_ONBOARDING,
    )

    # WP-6b: document branding + the "dealership default" half of the
    # correspondence-language rule (Customers FR-13 / ADR-051). logo_url is
    # an external reference only — this package builds no upload/hosting
    # mechanism, that's a WP-6c admin-screen concern; a dealership with no
    # logo renders an initials mark in brand_primary_color instead (same
    # fallback the frontend shell's own BrandMark already uses). Every
    # existing row is backfilled to purple[6] (#7C3AED, the shipped default
    # brand colour, frontend/packages/ui-kit/src/tokens.ts) and to "de" —
    # both placeholders a real dealership admin is expected to override,
    # not a considered per-dealership choice.
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_primary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#7C3AED")
    default_correspondence_language: Mapped[SwissLanguage] = mapped_column(
        SAEnum(SwissLanguage, native_enum=False, length=8), nullable=False, default=SwissLanguage.DE
    )

    # WP-7 PR-3 (ADR-057): the ONE VAT figure in the whole system — no
    # vatTreatment field exists anywhere. VAT is a single line on a printed
    # document only, computed here; never a per-vehicle attribute. Nullable
    # because a brand-new dealership hasn't configured it yet, not because
    # it's ever optional once trading.
    vat_rate: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 2), nullable=True)

    # WP-7 PR-7 (FR-I-14) — genuinely dealer-configurable, unlike the
    # fixed 0-60/61-120/121+ ageingBucket grid colour cue: a notification-
    # only consumer of the same "days in stock" number. None means "use
    # the default (30/60/90)," resolved by the caller, not baked in here.
    ageing_alert_thresholds: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    @property
    def address(self) -> dict[str, str]:
        """Read-side convenience: flat address_* columns as a nested dict
        matching app.platform.schemas.dealership.DealershipAddress. Writes go
        through the service layer, which unpacks the nested schema back into
        columns.
        """

        return {
            "street": self.address_street,
            "house_number": self.address_house_number,
            "postal_code": self.address_postal_code,
            "locality": self.address_locality,
            "canton": self.address_canton,
            "country": self.address_country,
        }


class Location(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "location"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    address_street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_house_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address_postal_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    address_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_canton: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Cantonal working-calendar reference (WP-5 addendum note). Empty in v1 —
    # nothing reads this until Aftersales ships, but retrofitting it onto a
    # year of real location data is the version nobody enjoys.
    calendar_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
