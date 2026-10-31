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

Both ends are platform-owned, so the foreign key to dealership is real (as on
`dealership_bank_account`). `uploaded_by` is the user who uploaded the file
(an actor column, reconciled against `user.id` nightly — KAN-243).
"""

import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("dealership_id", "kind", name="uq_branding_asset_dealership_kind"),)

    dealership_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dealership.id"), nullable=False)
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
