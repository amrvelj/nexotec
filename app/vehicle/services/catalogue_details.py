"""KAN-211 (ADR-123): a variant's licensed details — options, colours, tyre
dimensions, images and the energy label — load **once per Betrieb, when a
car is opened**, never on a schedule, and are refreshed only by «Neu laden»
on a stock car's technical data (dealer manager, audited).

Every detail is its own provider request, its own `call_capability` block and
its own savepoint, and ends in its own state (`VariantDetailState`):

- `present` — loaded and stored, possibly with no rows (the provider has no
  colours for most brands, FR-C-07);
- `not_in_contract` — the provider refused it (403), or «Test connection»
  recorded the licence option as absent, in which case nothing is sent;
- `unavailable` — a timeout, a 5xx, an open breaker: retried on the next open;
- *not loaded* — no row: this Betrieb never asked (no connection, or the
  variant has no key at its provider).

A refused or failed detail never discards the car or the other details
(KAN-212): the savepoint rolls back that detail's rows alone, the state says
what happened, and the next open asks again only for what is `unavailable`.
The breaker is charged per failed block as for any other call; a 403 never
charges it (`.claude/rules/vehicle.md`, gateway decision).

What is asked, per first open of a car (ADR-123; the identAPI has four
services behind these five details): the variant with its colours
(`vehiclecatalog`, one request), the options (`vehicleoption_catalog`, every
page of the one car — the one bounded walk over pages in the codebase,
capped in the adapter), the tyres (`tyresandrims_typeapproval`, both seasons
in one request), the images (`image_vehicle`, every size in one request) and
the energy label (`energylabel_info`). The second open sends nothing.

Storage is tenant-scoped (ADR-013) — except the energy label, public Swiss
data stored once for every Betrieb as that year's rating with
`source = auto_i_dat`, filling an absence only (ADR-042; Anto, 2026-11-08).
Data a load stored is kept indefinitely (KAN-487). The variant row is locked
for the load, so two tabs opening the same car at once send one set of
requests, not two.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.integration.public import (
    IntegrationConnection,
    ProviderAdapter,
    ProviderGatewayError,
    ProviderNotEntitledError,
    call_capability,
    get_entitlement,
)
from app.platform.public import get_active_reference_value_codes
from app.vehicle.models.catalogue import ModelVariant, VariantOption, VariantOptionRelation
from app.vehicle.models.catalogue_mirror import (
    CatalogueDetail,
    ColourCache,
    DetailState,
    ImageRef,
    TyreSpecCache,
    VariantDetailState,
)
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.provider import ProviderEntityRef, VariantProviderCode
from app.vehicle.services.catalogue_sync import (
    COLOUR_AND_TYRE_CODE_GROUPS,
    find_enabled_vehicle_data_connection,
    resolve_colour_or_tyre_code,
    sync_option_equipment_features,
    upsert_model_variant,
)
from app.vehicle.services.plate import swiss_today

logger = logging.getLogger("app.vehicle.catalogue_details")

_COLOUR_AREA, _AXLE_POSITION, _TYRE_TYPE = COLOUR_AND_TYRE_CODE_GROUPS
_ENTITY_TYPE_MODEL_VARIANT: Final = "model_variant"
_ENERGY_LABEL_SOURCE: Final = "auto_i_dat"
_ENERGY_LABEL_LIST: Final = "energy_efficiency_category"
# The `option_relation_type` reference values (a3d9c1e58f27) the identAPI's
# relation lists map onto: a pack's contents, an exclusion, a price in
# combination. Stored and shown, never enforced (ADR-072).
_RELATION_CONTAINS: Final = "contains"
_RELATION_EXCLUDES: Final = "excludes"
_RELATION_PRICED_WITH: Final = "price_in_combination_with"

# The identAPI licence option each detail is covered by (KAN-488's probes):
# a `granted = False` row for it means the provider would answer 403, so
# nothing is sent and the detail is `not_in_contract` at once.
DETAIL_CAPABILITY: Final[dict[CatalogueDetail, str]] = {
    CatalogueDetail.COLOURS: "ident_basis",
    CatalogueDetail.OPTIONS: "ausstattung",
    CatalogueDetail.IMAGES: "ausstattung",
    CatalogueDetail.TYRE_SPECS: "pneu",
    CatalogueDetail.ENERGY_LABEL: "energielabel",
}
# The order of the requests on a first open; colours first because that
# request also refreshes the master data (fill-if-NULL, KAN-83).
DETAILS: Final[tuple[CatalogueDetail, ...]] = (
    CatalogueDetail.COLOURS,
    CatalogueDetail.OPTIONS,
    CatalogueDetail.TYRE_SPECS,
    CatalogueDetail.IMAGES,
    CatalogueDetail.ENERGY_LABEL,
)
# Written as `error` when the probe, not the provider, decided the state.
ENTITLEMENT_ABSENT: Final = "entitlement_absent"


@dataclass(frozen=True)
class DetailStates:
    """What `load_on_open` found or decided, per detail; `None` is *not loaded*."""

    rows: dict[CatalogueDetail, VariantDetailState]

    def state_of(self, detail: CatalogueDetail) -> VariantDetailState | None:
        return self.rows.get(detail)


def detail_states(db: Session, *, tenant_id: uuid.UUID, model_variant_id: uuid.UUID) -> DetailStates:
    rows = db.scalars(
        select(VariantDetailState).where(
            VariantDetailState.tenant_id == tenant_id, VariantDetailState.model_variant_id == model_variant_id
        )
    ).all()
    return DetailStates(rows={row.detail: row for row in rows})


def provider_key_of(db: Session, *, variant: ModelVariant, provider_code: str) -> str | None:
    """The variant's FzKey at this provider, or `None` for a variant another
    provider (the demo, say) brought in — nothing can be asked for it."""

    return db.scalar(
        select(ProviderEntityRef.provider_key).where(
            ProviderEntityRef.entity_type == _ENTITY_TYPE_MODEL_VARIANT,
            ProviderEntityRef.entity_id == variant.id,
            ProviderEntityRef.provider == provider_code,
        )
    )


def _raw_vehicle_kind(db: Session, *, variant: ModelVariant, provider_code: str) -> str:
    """The provider's own vehicle-kind code for this variant (`type`, `01`
    on the identAPI), the qualifier every code-map row is keyed by
    (`ProviderCodeMap.vehicle_kind` is the RAW code, never the canonical
    kind). Recorded with the variant's raw codes on every visit (KAN-83);
    empty for a variant another provider brought in — its codes then resolve
    under no kind and write no gap, as `_resolve_code` rules."""

    return (
        db.scalar(
            select(VariantProviderCode.vehicle_kind).where(
                VariantProviderCode.model_variant_id == variant.id, VariantProviderCode.provider == provider_code
            )
        )
        or ""
    )


def _set_state(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    variant: ModelVariant,
    detail: CatalogueDetail,
    state: DetailState,
    error: str | None,
    rows: dict[CatalogueDetail, VariantDetailState],
) -> VariantDetailState:
    row = rows.get(detail)
    if row is None:
        row = VariantDetailState(tenant_id=tenant_id, model_variant_id=variant.id, detail=detail)
        db.add(row)
        rows[detail] = row
    row.state = state
    row.error = error
    row.loaded_at = utcnow()
    db.flush()
    return row


def _type_approval_number(variant: ModelVariant) -> str | None:
    numbers = sorted(link.type_approval.type_approval_number for link in variant.type_approval_links)
    return numbers[0] if numbers else None


def _options_model_year(variant: ModelVariant) -> int:
    """The identAPI's model year for the option list: a car still in
    production takes `0`, the provider's "currently available equipment";
    a discontinued one its last model year."""

    return variant.model_year_to or 0


def _stored_option_year(variant: ModelVariant) -> int:
    """What the stored options stand for: the year asked, or — for the
    provider's "currently available" answer — the current model year. Never
    the first production year of a car still built: its 2021 options are not
    today's."""

    return variant.model_year_to or swiss_today().year


# -- the loaders: one per detail, each replacing this Betrieb's rows -----------------


def _load_colours(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    adapter: ProviderAdapter,
) -> None:
    answer = adapter.fetch_variant_with_colours(fz_key)
    # The same request carries the master data: a revisit fills what is still
    # NULL and records the raw codes (KAN-83), never overwriting a value.
    upsert_model_variant(db, provider_code=provider_code, master=answer.master)
    kind = answer.master.vehicle_kind_code
    existing = {
        row.colour_code: row
        for row in db.scalars(
            select(ColourCache).where(ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == variant.id)
        )
    }
    seen: set[str] = set()
    for colour in answer.colours:
        seen.add(colour.colour_code)
        # KAN-75: resolved before the row is touched; an unknown code is a
        # gap and leaves `colour_type` NULL (the colour is then not offered).
        colour_type = resolve_colour_or_tyre_code(
            db,
            provider_code=provider_code,
            vehicle_kind_code=kind,
            code_group=_COLOUR_AREA,
            raw_code=colour.colour_area_code,
        )
        row = existing.get(colour.colour_code)
        if row is None:
            row = ColourCache(tenant_id=tenant_id, model_variant_id=variant.id, colour_code=colour.colour_code)
            db.add(row)
        row.description = colour.description
        row.colour_area_code = colour.colour_area_code
        row.colour_type = colour_type
        row.price = colour.price
    for code, row in existing.items():
        if code not in seen:
            db.delete(row)
    db.flush()


def _load_options(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    adapter: ProviderAdapter,
) -> None:
    options = adapter.fetch_options(fz_key, model_year=_options_model_year(variant))
    kind = _raw_vehicle_kind(db, variant=variant, provider_code=provider_code)
    existing = {
        row.option_code: row
        for row in db.scalars(
            select(VariantOption).where(
                VariantOption.tenant_id == tenant_id, VariantOption.model_variant_id == variant.id
            )
        )
    }
    # Relations are rebuilt from this answer: the old ones go first, so a
    # relation the provider dropped does not survive.
    for relation in db.scalars(
        select(VariantOptionRelation).where(
            VariantOptionRelation.tenant_id == tenant_id, VariantOptionRelation.model_variant_id == variant.id
        )
    ):
        db.delete(relation)
    db.flush()
    model_year = _stored_option_year(variant)
    rows: dict[str, VariantOption] = {}
    for option in options:
        row = existing.get(option.option_code)
        if row is None:
            row = VariantOption(tenant_id=tenant_id, model_variant_id=variant.id, option_code=option.option_code)
            db.add(row)
        row.description = option.description
        row.option_group = option.option_group
        row.price = option.price
        row.is_included = option.is_included
        row.is_package = option.is_package
        row.model_year = model_year
        row.manufacturer_code = option.manufacturer_code
        db.flush()  # the id the feature and relation rows reference
        sync_option_equipment_features(
            db,
            tenant_id=tenant_id,
            provider_code=provider_code,
            vehicle_kind_code=kind,
            variant_option=row,
            feature_codes=option.equipment_feature_codes,
        )
        rows[option.option_code] = row
    for code, row in existing.items():
        if code not in rows:
            for link in list(row.equipment_feature_links):
                db.delete(link)
            db.delete(row)
    db.flush()
    # ADR-072: stored and shown, never enforced. A relation to an option the
    # provider did not list for this car names nothing we hold and is skipped.
    for option in options:
        from_row = rows[option.option_code]
        for code in option.pack_option_codes:
            _add_relation(
                db,
                tenant_id=tenant_id,
                variant=variant,
                model_year=model_year,
                from_row=from_row,
                to_row=rows.get(code),
                relation_type=_RELATION_CONTAINS,
                price=None,
            )
        for code in option.excluded_option_codes:
            _add_relation(
                db,
                tenant_id=tenant_id,
                variant=variant,
                model_year=model_year,
                from_row=from_row,
                to_row=rows.get(code),
                relation_type=_RELATION_EXCLUDES,
                price=None,
            )
        for priced in option.priced_with:
            _add_relation(
                db,
                tenant_id=tenant_id,
                variant=variant,
                model_year=model_year,
                from_row=from_row,
                to_row=rows.get(priced.option_code),
                relation_type=_RELATION_PRICED_WITH,
                price=priced.price,
            )
    db.flush()


def _add_relation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    variant: ModelVariant,
    model_year: int,
    from_row: VariantOption,
    to_row: VariantOption | None,
    relation_type: str,
    price,
) -> None:
    if to_row is None or to_row.id == from_row.id:
        return
    db.add(
        VariantOptionRelation(
            tenant_id=tenant_id,
            model_variant_id=variant.id,
            model_year=model_year,
            from_option_id=from_row.id,
            to_option_id=to_row.id,
            relation_type=relation_type,
            price_in_combination=price,
        )
    )


def _load_tyre_specs(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    adapter: ProviderAdapter,
) -> None:
    number = _type_approval_number(variant)
    tyres = adapter.fetch_tyre_specs(type_approval_number=number) if number else []
    kind = _raw_vehicle_kind(db, variant=variant, provider_code=provider_code)
    existing = {
        (row.axle_code, row.season_code): row
        for row in db.scalars(
            select(TyreSpecCache).where(
                TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == variant.id
            )
        )
    }
    seen: set[tuple[str | None, str | None]] = set()
    for tyre in tyres:
        key = (tyre.axle_code, tyre.season_code)
        if key in seen:
            # One row per axle and season (the table's key): a second
            # dimension for the same pair — another gearbox's — is not a
            # second row here; the first one listed is kept.
            continue
        seen.add(key)
        # Resolved before the row is touched: the lookup may flush, and a new
        # row must not reach the database half-filled (KAN-75's own order).
        axle = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_AXLE_POSITION, raw_code=tyre.axle_code
        )
        season = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_TYRE_TYPE, raw_code=tyre.season_code
        )
        row = existing.get(key)
        if row is None:
            row = TyreSpecCache(
                tenant_id=tenant_id,
                model_variant_id=variant.id,
                axle_code=tyre.axle_code,
                season_code=tyre.season_code,
                size=tyre.size,
            )
            db.add(row)
        row.axle = axle
        row.season = season
        row.size = tyre.size
        row.load_index = tyre.load_index
        row.speed_rating = tyre.speed_rating
        row.remark = tyre.remark
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
    db.flush()


def _load_images(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    adapter: ProviderAdapter,
) -> None:
    images = adapter.fetch_images(fz_key)
    existing = {
        row.image_key: row
        for row in db.scalars(
            select(ImageRef).where(ImageRef.tenant_id == tenant_id, ImageRef.model_variant_id == variant.id)
        )
    }
    seen: set[str] = set()
    for image in images:
        seen.add(image.image_key)
        row = existing.get(image.image_key)
        if row is None:
            row = ImageRef(tenant_id=tenant_id, model_variant_id=variant.id, image_key=image.image_key)
            db.add(row)
        row.bild_typ = image.bild_typ
        row.bild_art = image.bild_art
        row.sequence = image.sequence
        row.image_url = image.image_url
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
    db.flush()


def _load_energy_label(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    adapter: ProviderAdapter,
) -> None:
    """The provider's A–G class per calculation year, stored once for every
    Betrieb (ADR-042: a value as of a year) with `source = auto_i_dat`,
    filling an absence only — a BFE row, or any row already held for the
    year, is left alone. A letter the `energy_efficiency_category` list
    does not know is left out."""

    number = _type_approval_number(variant)
    labels = adapter.fetch_energy_label(type_approval_number=number) if number else []
    if not labels:
        return
    known = get_active_reference_value_codes(db, _ENERGY_LABEL_LIST) or set()
    for label in labels:
        code = label.energy_class.strip().lower()
        if code not in known:
            continue
        held = db.scalar(
            select(ModelVariantEnergyRating.id).where(
                ModelVariantEnergyRating.model_variant_id == variant.id,
                ModelVariantEnergyRating.rating_year == label.calculation_year,
            )
        )
        if held is not None:
            continue
        db.add(
            ModelVariantEnergyRating(
                model_variant_id=variant.id,
                rating_year=label.calculation_year,
                energy_efficiency_category=code,
                source=_ENERGY_LABEL_SOURCE,
            )
        )
        db.flush()


_Loader = Callable[..., None]
_LOADERS: Final[dict[CatalogueDetail, _Loader]] = {
    CatalogueDetail.COLOURS: _load_colours,
    CatalogueDetail.OPTIONS: _load_options,
    CatalogueDetail.TYRE_SPECS: _load_tyre_specs,
    CatalogueDetail.IMAGES: _load_images,
    CatalogueDetail.ENERGY_LABEL: _load_energy_label,
}


# -- the two entry points -------------------------------------------------------------


def load_on_open(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    variant: ModelVariant,
    actor_id: uuid.UUID | None,
    purpose: str = "catalogue_details_on_open",
    force: bool = False,
) -> DetailStates:
    """Loads every detail this Betrieb has not stored — or, with `force`,
    every detail afresh (`reload_details`) — and returns the states. Each
    detail is its own gateway block and savepoint; a refused or failed one
    ends in its state and the loop goes on. Commits: the states and the rows
    are facts about what was asked and answered, whatever the caller does
    next. Without a connection, or for a variant unknown at the provider,
    nothing is asked and nothing is written."""

    states = detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return states
    connection, provider_code = found
    fz_key = provider_key_of(db, variant=variant, provider_code=provider_code)
    if fz_key is None:
        return states
    wanted = [
        detail
        for detail in DETAILS
        if force or states.state_of(detail) is None or states.state_of(detail).state == DetailState.UNAVAILABLE  # type: ignore[union-attr]
    ]
    if not wanted:
        return states
    # Serialises two opens of the same car: the second waits here, then
    # re-reads the states and finds the first one's work (Postgres; the
    # SQLite lane has no row locks and never counts as verification).
    db.execute(select(ModelVariant.id).where(ModelVariant.id == variant.id).with_for_update())
    states = detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
    rows = states.rows
    for detail in wanted:
        row = rows.get(detail)
        if not force and row is not None and row.state != DetailState.UNAVAILABLE:
            continue  # loaded by the open that held the lock before us
        _load_one(
            db,
            tenant_id=tenant_id,
            connection=connection,
            provider_code=provider_code,
            variant=variant,
            fz_key=fz_key,
            detail=detail,
            actor_id=actor_id,
            purpose=purpose,
            rows=rows,
        )
    db.commit()
    return DetailStates(rows=rows)


def _load_one(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection: IntegrationConnection,
    provider_code: str,
    variant: ModelVariant,
    fz_key: str,
    detail: CatalogueDetail,
    actor_id: uuid.UUID | None,
    purpose: str,
    rows: dict[CatalogueDetail, VariantDetailState],
) -> None:
    def set_state(state: DetailState, error: str | None) -> None:
        _set_state(db, tenant_id=tenant_id, variant=variant, detail=detail, state=state, error=error, rows=rows)

    entitlement = get_entitlement(db, connection_id=connection.id, capability_code=DETAIL_CAPABILITY[detail])
    if entitlement is not None and not entitlement.granted:
        set_state(DetailState.NOT_IN_CONTRACT, ENTITLEMENT_ABSENT)
        return
    try:
        with (
            call_capability(
                db,
                connection=connection,
                capability=f"catalogue_detail_{detail.value}",
                actor_id=actor_id,
                purpose=purpose,
            ) as adapter,
            # Rolled back on any failure before the gateway sees it, so a
            # refused detail leaves nothing half-written (KAN-90's rule) and
            # the other details' rows are untouched (KAN-212).
            db.begin_nested(),
        ):
            _LOADERS[detail](
                db, tenant_id=tenant_id, provider_code=provider_code, variant=variant, fz_key=fz_key, adapter=adapter
            )
            set_state(DetailState.PRESENT, None)
    except ProviderNotEntitledError as exc:
        set_state(DetailState.NOT_IN_CONTRACT, type(exc).__name__)
    except ProviderGatewayError as exc:
        # A transport failure, an open breaker, a refused login, a connection
        # not configured for a call with nobody to enrol: asked again on the
        # next open. The class name only — a provider's message can echo
        # what it was sent.
        logger.info("catalogue detail %s of variant %s unavailable: %s", detail.value, variant.id, type(exc).__name__)
        set_state(DetailState.UNAVAILABLE, type(exc).__name__)


def reload_details(
    db: Session, *, tenant_id: uuid.UUID, variant: ModelVariant, actor_id: uuid.UUID, reason: str | None = None
) -> DetailStates:
    """«Neu laden» (ticket point 4): one fresh request per detail, replacing
    this Betrieb's stored rows, recorded in the audit log with the actor.
    Nowhere else does anything refresh a detail."""

    before = {
        detail.value: (row.state.value, row.loaded_at.isoformat())
        for detail, row in detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id).rows.items()
    }
    states = load_on_open(
        db, tenant_id=tenant_id, variant=variant, actor_id=actor_id, purpose="catalogue_details_reload", force=True
    )
    record_audit_event(
        db,
        entity_type="vehicle_variant_details",
        entity_id=variant.id,
        tenant_id=tenant_id,
        action="reload",
        actor_id=actor_id,
        before=before,
        after={detail.value: (row.state.value, row.loaded_at.isoformat()) for detail, row in states.rows.items()},
        reason=reason or "KAN-211: «Neu laden» on the stock car's technical data",
    )
    db.commit()
    return states
