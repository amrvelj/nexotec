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

Both ends are platform-owned, so the foreign keys to dealership and location
are real (as on `dealership_bank_account`). `uploaded_by` is the user who uploaded the file
(an actor column, reconciled against `user.id` nightly — KAN-243).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, utcnow
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class BrandingAssetKind(str, enum.Enum):
    PRIMARY = "primary"
    MONO_DARK = "mono_dark"
    FAVICON = "favicon"


class BrandingAsset(PrimaryKeyMixin, Base):
    __tablename__ = "branding_asset"
    __table_args__ = (
        Index(
            "uq_branding_asset_dealership_kind",
            "dealership_id",
            "kind",
            unique=True,
            postgresql_where=text("location_id IS NULL"),
            sqlite_where=text("location_id IS NULL"),
        ),
        Index(
            "uq_branding_asset_location_kind",
            "location_id",
            "kind",
            unique=True,
            postgresql_where=text("location_id IS NOT NULL"),
            sqlite_where=text("location_id IS NOT NULL"),
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
