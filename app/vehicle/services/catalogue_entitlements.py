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
from app.vehicle.models.catalogue import VariantOption, VariantOptionRelation
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
    id: uuid.UUID
    option_code: str
    description: str
    option_group: str | None
    price: Decimal | None
    # KAN-43 (C-E) — Inklusiv / PackCode / SuchCode. `equipment_features`
    # is the provider's own mapping, shown alongside the option — the
    # advisor may still correct it (both here and, separately, on the
    # selected `VehicleConfigurationOption`; see Q-C-5).
    is_included: bool
    is_package: bool
    equipment_features: list[str]


@dataclass(frozen=True)
class ColourSpec:
    colour_code: str
    description: str
    colour_type: str
    # KAN-43 (C-E) / FR-C-07 — the surcharge, a price line in build mode.
    price: Decimal | None


@dataclass(frozen=True)
class TyreSpec:
    axle: str
    size: str
    load_index: str | None
    speed_rating: str | None
    # KAN-43 (C-E) / FR-C-08 — PneuTyp / BemDe, shown with the dimension.
    season: str | None
    remark: str | None


@dataclass(frozen=True)
class ImageSpec:
    image_key: str
    bild_typ: str
    bild_art: str
    sequence: int


@dataclass(frozen=True)
class OptionRelationSpec:
    """FR-C-06's inline relation rendering — "contained in Pack Family ·
    excludes Sportsitze · CHF 400 in combination with Klimaautomat ·
    becomes standard with …". `from_option_id` groups relations under the
    option they attach to; `to_option_code`/`to_option_description` are
    denormalised here so the caller never needs a second lookup to render
    the sentence. ADR-072 — stored and shown, never enforced: this list is
    display data, and selecting `from_option_id` alongside a `to_option_id`
    it `excludes` is a warning shown by the caller, never a rejected
    selection.
    """

    from_option_id: uuid.UUID
    to_option_id: uuid.UUID
    to_option_code: str
    to_option_description: str
    relation_type: str
    price_in_combination: Decimal | None


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
    # Empty whenever `packages_available` is False — relations ARE the
    # "package and compatibility data" FR-C-06's own degradation message
    # refers to, so they degrade exactly like `option_group` above rather
    # than needing a second flag.
    option_relations: list[OptionRelationSpec]


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
            option_relations=[],
        )

    option_rows = db.scalars(
        select(VariantOption).where(VariantOption.tenant_id == tenant_id, VariantOption.model_variant_id == model_variant_id)
    ).all()
    options = [
        OptionSpec(
            id=row.id, option_code=row.option_code, description=row.description,
            # Flattened, never grouped, without the packages entitlement —
            # never a fabricated "package" the dealer isn't licensed to see.
            option_group=(row.option_group if entitlements.packages else None), price=row.price,
            is_included=row.is_included, is_package=row.is_package,
            equipment_features=[link.feature_value_code for link in row.equipment_feature_links],
        )
        for row in option_rows
    ]
    options_by_id = {row.id: row for row in option_rows}

    colour_rows = db.scalars(
        select(ColourCache).where(ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == model_variant_id)
    ).all()
    colours = [
        ColourSpec(colour_code=row.colour_code, description=row.description, colour_type=row.colour_type, price=row.price)
        for row in colour_rows
    ]

    tyre_rows = db.scalars(
        select(TyreSpecCache).where(TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == model_variant_id)
    ).all()
    tyre_specs = [
        TyreSpec(
            axle=row.axle, size=row.size, load_index=row.load_index, speed_rating=row.speed_rating,
            season=row.season, remark=row.remark,
        )
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

    # Relations ARE the "package and compatibility data" FR-C-06's own
    # degradation message covers — without the entitlement, never queried,
    # matching `option_group`'s own flattening above.
    option_relations: list[OptionRelationSpec] = []
    if entitlements.packages:
        relation_rows = db.scalars(
            select(VariantOptionRelation).where(
                VariantOptionRelation.tenant_id == tenant_id,
                VariantOptionRelation.model_variant_id == model_variant_id,
            )
        ).all()
        option_relations = [
            OptionRelationSpec(
                from_option_id=relation.from_option_id, to_option_id=relation.to_option_id,
                to_option_code=options_by_id[relation.to_option_id].option_code
                if relation.to_option_id in options_by_id
                else "",
                to_option_description=options_by_id[relation.to_option_id].description
                if relation.to_option_id in options_by_id
                else "",
                relation_type=relation.relation_type, price_in_combination=relation.price_in_combination,
            )
            for relation in relation_rows
        ]

    return CatalogueSpecificationResult(
        has_catalogue_match=True, has_provider_connection=entitlements.has_connection,
        packages_available=entitlements.packages, images_available=entitlements.images,
        dealer_can_upload_images=dealer_can_upload_images, options=options, colours=colours, tyre_specs=tyre_specs,
        images=images, option_relations=option_relations,
    )
