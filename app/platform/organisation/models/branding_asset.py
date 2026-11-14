"""branding_asset (KAN-205; Dealer Administration PRD, Logo and brand
assets): the Betrieb's logo files, one row per variant.

`primary` · `mono_dark` (for a colour-on-white letterhead) · `favicon`. Only
`primary` is mandatory; a variant without its own file falls back to it
(`services/branding.py::resolve_asset`). The file itself lives in the object
store (`app/core/object_store.py`) under a tenant-partitioned key,
`dealerships/<dealership id>/branding/<kind>/<asset id>` — this row holds the
key and what was judged on upload (type, pixel size, bytes), never the bytes
and never base64. A replaced file gets a new row and a new key, so the row
and the object it names change together.

KAN-206: a location may carry its own letterhead logo (PRD: a per-location
override of the address block and logo only) — a `primary` row carrying the
location's id. The Betrieb's own variants carry none; one row per kind for
the Betrieb and one per kind for each location (two partial unique indexes).

KAN-225: the publishing profile's imagery (PRD Publishing profile, Imagery)
is three more kinds — `premises` · `team` · `hero` — always carrying a
location (a group listing shows the site the car is at), several per kind,
ordered by `position`, with exactly one `is_primary` per location and kind
once any exists. The logo kinds keep their one-row-per-kind indexes; the
imagery kinds are left out of them (`LOGO_KINDS`) and have their own.
Imagery is JPEG or PNG (a photograph); a logo stays SVG or PNG.

Both ends are platform-owned, so the foreign keys to dealership and location
are real (as on `dealership_bank_account`). `uploaded_by` is the user who uploaded the file
(an actor column, reconciled against `user.id` nightly — KAN-243).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, false, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class BrandingAssetKind(str, enum.Enum):
    PRIMARY = "primary"
    MONO_DARK = "mono_dark"
    FAVICON = "favicon"
    # KAN-225 — publishing imagery, per location.
    PREMISES = "premises"
    TEAM = "team"
    HERO = "hero"


class LogoKind(str, enum.Enum):
    """The logo variants — what the Branding screen's routes take. The
    imagery kinds are the publishing profile's (its own routes)."""

    PRIMARY = "primary"
    MONO_DARK = "mono_dark"
    FAVICON = "favicon"


class PublishingImageKind(str, enum.Enum):
    PREMISES = "premises"
    TEAM = "team"
    HERO = "hero"


LOGO_KINDS = (BrandingAssetKind.PRIMARY, BrandingAssetKind.MONO_DARK, BrandingAssetKind.FAVICON)
IMAGERY_KINDS = (BrandingAssetKind.PREMISES, BrandingAssetKind.TEAM, BrandingAssetKind.HERO)
_LOGO_KINDS_SQL = "kind IN ('primary', 'mono_dark', 'favicon')"
_IMAGERY_KINDS_SQL = "kind IN ('premises', 'team', 'hero')"


class BrandingAsset(PrimaryKeyMixin, Base):
    __tablename__ = "branding_asset"
    __table_args__ = (
        Index(
            "uq_branding_asset_dealership_kind",
            "dealership_id",
            "kind",
            unique=True,
            postgresql_where=text(f"location_id IS NULL AND {_LOGO_KINDS_SQL}"),
            sqlite_where=text(f"location_id IS NULL AND {_LOGO_KINDS_SQL}"),
        ),
        Index(
            "uq_branding_asset_location_kind",
            "location_id",
            "kind",
            unique=True,
            postgresql_where=text(f"location_id IS NOT NULL AND {_LOGO_KINDS_SQL}"),
            sqlite_where=text(f"location_id IS NOT NULL AND {_LOGO_KINDS_SQL}"),
        ),
        # KAN-225: one primary image per location and imagery kind.
        Index(
            "uq_branding_asset_imagery_primary",
            "location_id",
            "kind",
            unique=True,
            postgresql_where=text(f"is_primary AND {_IMAGERY_KINDS_SQL}"),
            sqlite_where=text(f"is_primary AND {_IMAGERY_KINDS_SQL}"),
        ),
        CheckConstraint(
            f"NOT ({_IMAGERY_KINDS_SQL}) OR (location_id IS NOT NULL AND position IS NOT NULL)",
            name="ck_branding_asset_imagery_has_location",
        ),
    )

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
    # KAN-206: NULL — the Betrieb's own logo; set — that site's letterhead logo.
    location_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("location.id"), nullable=True)
    kind: Mapped[BrandingAssetKind] = mapped_column(StoredEnum(BrandingAssetKind, length=16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Pixel size of a PNG; an SVG's intrinsic size from width/height or the
    # viewBox, null when it declares none (a vector has no resolution).
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    # KAN-225 — imagery only: the order on the site (0 first) and the one
    # image a channel shows first. NULL / false on a logo row.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
