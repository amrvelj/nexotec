"""Entitlement-based degradation for the catalogue mirror (WP-6 PR-5).
Reads `app.integration.public.get_entitlement` — never
`IntegrationEntitlement` directly.

No entitlement row for a capability defaults to **granted** (optimistic):
probing per-capability entitlements is explicitly not built by PR-2/PR-3
(`services/gateway.py`'s own docstring: "probing per-capability
entitlements... is PR-5's job", and PR-2's own `/test` action only probes
`system_watermark`) — so a freshly-connected account behaves as fully
capable until something explicit says otherwise, matching this
codebase's existing "don't degrade without cause" bias (the same
reasoning behind "no provider contract keeps a fully usable module"). A
future capability-probe step, or a human declaring a restriction, writes
`granted=False` and this function starts respecting it immediately — no
code change needed here.

A dealer with **no** connection at all degrades identically to one whose
connection lacks a specific entitlement — from a caller's perspective
both mean "this capability is unavailable right now", so `has_connection
=False` collapses every capability to unavailable rather than being
treated as a separate third state.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integration.public import get_entitlement
from app.vehicle.models.catalogue import VariantOption
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, TyreSpecCache
from app.vehicle.services.catalogue_sync import find_enabled_vehicle_data_connection


@dataclass(frozen=True)
class CatalogueEntitlements:
    has_connection: bool
    images: bool
    packages: bool
    valuation: bool
    forecast: bool


@dataclass(frozen=True)
class OptionSpec:
    option_code: str
    description: str
    option_group: str | None
    price: Decimal | None


@dataclass(frozen=True)
class ColourSpec:
    colour_code: str
    description: str
    colour_type: str


@dataclass(frozen=True)
class TyreSpec:
    axle: str
    size: str
    load_index: str | None
    speed_rating: str | None


@dataclass(frozen=True)
class ImageSpec:
    image_key: str
    bild_typ: str
    bild_art: str
    sequence: int


@dataclass(frozen=True)
class CatalogueSpecificationResult:
    has_catalogue_match: bool
    has_provider_connection: bool
    packages_available: bool
    images_available: bool
    dealer_can_upload_images: bool
    options: list[OptionSpec]
    colours: list[ColourSpec]
    tyre_specs: list[TyreSpec]
    images: list[ImageSpec]


def _is_granted(db: Session, *, connection_id: uuid.UUID, capability_code: str) -> bool:
    entitlement = get_entitlement(db, connection_id=connection_id, capability_code=capability_code)
    return True if entitlement is None else entitlement.granted


def get_catalogue_entitlements(db: Session, *, tenant_id: uuid.UUID) -> CatalogueEntitlements:
    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return CatalogueEntitlements(has_connection=False, images=False, packages=False, valuation=False, forecast=False)
    connection, _provider_code = found
    return CatalogueEntitlements(
        has_connection=True,
        images=_is_granted(db, connection_id=connection.id, capability_code="images"),
        packages=_is_granted(db, connection_id=connection.id, capability_code="packages"),
        valuation=_is_granted(db, connection_id=connection.id, capability_code="valuation"),
        forecast=_is_granted(db, connection_id=connection.id, capability_code="forecast"),
    )


def get_catalogue_specification(
    db: Session, *, tenant_id: uuid.UUID, model_variant_id: uuid.UUID | None
) -> CatalogueSpecificationResult:
    """Reads the already-synced tenant-scoped mirror (PR-4's own tables) —
    never a live provider call. `model_variant_id=None` (an unmatched
    vehicle, `CatalogueMatchStatus.UNVERIFIED`) returns an empty-but-valid
    specification rather than a 404 — the screen stays usable, per FR-V-03.
    """

    entitlements = get_catalogue_entitlements(db, tenant_id=tenant_id)
    dealer_can_upload_images = not entitlements.images

    if model_variant_id is None:
        return CatalogueSpecificationResult(
            has_catalogue_match=False, has_provider_connection=entitlements.has_connection,
            packages_available=entitlements.packages, images_available=entitlements.images,
            dealer_can_upload_images=dealer_can_upload_images, options=[], colours=[], tyre_specs=[], images=[],
        )

    option_rows = db.scalars(
        select(VariantOption).where(VariantOption.tenant_id == tenant_id, VariantOption.model_variant_id == model_variant_id)
    ).all()
    options = [
        OptionSpec(
            option_code=row.option_code, description=row.description,
            # Flattened, never grouped, without the packages entitlement —
            # never a fabricated "package" the dealer isn't licensed to see.
            option_group=(row.option_group if entitlements.packages else None), price=row.price,
        )
        for row in option_rows
    ]

    colour_rows = db.scalars(
        select(ColourCache).where(ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == model_variant_id)
    ).all()
    colours = [
        ColourSpec(colour_code=row.colour_code, description=row.description, colour_type=row.colour_type)
        for row in colour_rows
    ]

    tyre_rows = db.scalars(
        select(TyreSpecCache).where(TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == model_variant_id)
    ).all()
    tyre_specs = [
        TyreSpec(axle=row.axle, size=row.size, load_index=row.load_index, speed_rating=row.speed_rating)
        for row in tyre_rows
    ]

    images: list[ImageSpec] = []
    if entitlements.images:
        image_rows = db.scalars(
            select(ImageRef).where(ImageRef.tenant_id == tenant_id, ImageRef.model_variant_id == model_variant_id)
        ).all()
        images = [
            ImageSpec(image_key=row.image_key, bild_typ=row.bild_typ, bild_art=row.bild_art, sequence=row.sequence)
            for row in image_rows
        ]

    return CatalogueSpecificationResult(
        has_catalogue_match=True, has_provider_connection=entitlements.has_connection,
        packages_available=entitlements.packages, images_available=entitlements.images,
        dealer_can_upload_images=dealer_can_upload_images, options=options, colours=colours, tyre_specs=tyre_specs,
        images=images,
    )
