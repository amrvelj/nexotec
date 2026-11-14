"""Dealer Administration › Branding (KAN-205; PRD Logo and brand assets):
the brand colours with their contrast verdict, where the logo appears, and
the logo variants. The PATCH is partial; the logo files themselves are
uploaded as raw bodies (`PUT …/branding/assets/{kind}`), never JSON."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.core.schemas import CamelModel
from app.platform.models.branding_asset import BrandingAssetKind
from app.platform.schemas.patch import refuse_null_on_not_null_fields
from app.platform.services.brand_colors import HEX_COLOR, WCAG_AA_NORMAL_TEXT, ContrastPairName


def _hex(value: str | None) -> str | None:
    if value is None:
        return None
    if not HEX_COLOR.match(value):
        raise ValueError("A colour is written #RRGGBB, e.g. #7C3AED.")
    return value.upper()


class BrandingPlacements(CamelModel):
    """Where the logo appears — each a switch of its own (PRD: "a dealer often
    wants the logo on a price tag but not in the app chrome")."""

    document_header: bool
    price_tag: bool
    listing: bool
    email_signature: bool
    app_header: bool


class BrandingPlacementsUpdate(CamelModel):
    document_header: bool | None = None
    price_tag: bool | None = None
    listing: bool | None = None
    email_signature: bool | None = None
    app_header: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> "BrandingPlacementsUpdate":
        refuse_null_on_not_null_fields(self)
        return self


class BrandingUpdate(CamelModel):
    primary_color: str | None = Field(default=None, description="#RRGGBB")
    # Null clears the secondary colour; leaving it out keeps it.
    secondary_color: str | None = Field(default=None, description="#RRGGBB, or null to clear it")
    placements: BrandingPlacementsUpdate | None = None

    @field_validator("primary_color", "secondary_color")
    @classmethod
    def _is_hex(cls, value: str | None) -> str | None:
        return _hex(value)

    @model_validator(mode="after")
    def _not_null(self) -> "BrandingUpdate":
        refuse_null_on_not_null_fields(self, nullable={"secondary_color"})
        return self


class ContrastPairRead(CamelModel):
    pair: ContrastPairName
    foreground: str
    background: str
    ratio: float
    passes: bool


class ContrastCheckRead(CamelModel):
    """WCAG AA for normal text (4.5:1), each pair judged on its own."""

    minimum_ratio: float = WCAG_AA_NORMAL_TEXT
    passes: bool
    pairs: list[ContrastPairRead]


class BrandingAssetRead(CamelModel):
    """One logo variant. `source` says which file a reader is served:
    `own` (this variant's file), `primary` (no file of its own, so the
    primary's — PRD: "the others fall back to it"), or `none` (no logo at
    all). The file fields describe this variant's own file and are null
    without one."""

    kind: BrandingAssetKind
    source: Literal["own", "primary", "none"]
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    byte_size: int | None = None
    uploaded_by: uuid.UUID | None = None
    uploaded_at: dt.datetime | None = None


class LocationLogoRead(CamelModel):
    """KAN-206: a site's own letterhead logo (PRD: the per-location override
    of the address block and logo only). Documents printed for that site
    show it in place of the Betrieb's primary logo."""

    location_id: uuid.UUID
    mime_type: str
    width: int | None = None
    height: int | None = None
    byte_size: int
    uploaded_by: uuid.UUID | None = None
    uploaded_at: dt.datetime


class BrandingRead(CamelModel):
    dealership_id: uuid.UUID
    primary_color: str
    secondary_color: str | None
    placements: BrandingPlacements
    assets: list[BrandingAssetRead]
    location_logos: list[LocationLogoRead] = Field(default_factory=list)
    contrast: ContrastCheckRead
    # The dealership's version: every branding write moves it, and every
    # branding write takes it as If-Match.
    version: int
