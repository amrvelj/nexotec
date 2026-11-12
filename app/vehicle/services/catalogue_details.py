"""KAN-211 (ADR-123): a variant's licensed details — options, colours, tyre
dimensions, images and the energy label — load **once per Betrieb, when a
car is opened**, never on a schedule, and are refreshed only by «Neu laden»
on a stock car's technical data (dealer manager, audited).

Every detail is its own provider request, its own `call_capability` block,
its own savepoint **and its own commit**, and ends in its own state
(`VariantDetailState`):

- `present` — loaded and stored, possibly with no rows (the provider has no
  colours for most brands, FR-C-07; a car the provider no longer knows; a
  request it refused as malformed — `error` says which);
- `not_in_contract` — the provider refused it (403), or «Test connection»
  recorded the licence option as absent, in which case nothing is sent;
  asked again once a later probe records the option as granted;
- `unavailable` — a timeout, a 5xx, an open breaker: asked again on the
  next open;
- *not loaded* — no row: this Betrieb never asked (no connection, or the
  variant has no key at its provider).

A refused or failed detail never discards the car or the other details
(KAN-212): each detail's rows and state are committed before the next one
is asked, the savepoint rolls back that detail's rows alone, and the next
open asks again only for what is `unavailable`. The breaker is charged per
failed block as for any other call; a 403 never charges it
(`.claude/rules/vehicle-gateway.md`, gateway decision). What a provider sends is
stored defensively: a key repeated in one answer is one row, a text longer
than its column is cut, an image whose URL does not fit is left out and
said so in the log.

What is asked, per first open of a car (ADR-123; the identAPI has four
services behind these five details): the variant with its colours
(`vehiclecatalog`, one request), the options (`vehicleoption_catalog`, every
page of the one car — the one bounded walk over pages in the codebase,
capped in the adapter), the tyres (`tyresandrims_typeapproval`, both seasons
in one request), the images (`image_vehicle`, every size in one request) and
the energy label (`energylabel_info`). The second open sends nothing.

Storage is tenant-scoped (ADR-013) — except the energy label, public Swiss
data stored once for every Betrieb as that year's rating with
`source = auto_i_dat`, filling an absence only (ADR-042; Anto, 2026-11-08),
and only where every gearbox the provider lists agrees on the class. Data a
load stored is kept indefinitely (KAN-487). Two opens of the same car by
the same Betrieb are serialised by a transaction-scoped advisory lock on
(Betrieb, variant), taken per detail on the request's own connection and
released by that detail's commit, so two tabs send one set; another Betrieb's open of the same car waits only
where both write the same shared row (a new mapping gap, a price year, a
year's label), and
the load itself holds no connection besides the request's own (the gateway's
brief session aside, KAN-90).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.integration.public import (
    IntegrationConnection,
    ProviderAdapter,
    ProviderGatewayError,
    ProviderNotEntitledError,
    ProviderRequestError,
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
from app.vehicle.models.energy_rating import ModelVariantEnergyLabel, ModelVariantEnergyRating
from app.vehicle.models.provider import ProviderEntityRef, VariantOptionProviderCode, VariantProviderCode
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
_OPTION_GROUP: Final = "option_group"
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
# nothing is sent and the detail is `not_in_contract` at once; a later probe
# that records it granted re-opens the detail.
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
# `error` notes a state can carry besides an exception's class name.
ENTITLEMENT_ABSENT: Final = "entitlement_absent"  # the probe, not the provider, decided
VARIANT_UNKNOWN_AT_PROVIDER: Final = "variant_unknown_at_provider"  # the key is gone at the provider
ENERGY_LABEL_AMBIGUOUS: Final = "energy_label_ambiguous"  # gearboxes disagree on the class for a year


class _NothingToLoad(Exception):
    """A detail the provider has nothing for — the car is no longer under its
    key. Ends in `present` with a note, so the next open does not pay for the
    same answer again; «Neu laden» asks afresh."""

    def __init__(self, note: str) -> None:
        self.note = note
        super().__init__(note)


class _LoadedWithNote(Exception):
    """The detail loaded, with something worth saying in `error`."""

    def __init__(self, note: str) -> None:
        self.note = note
        super().__init__(note)


@dataclass(frozen=True)
class DetailStates:
    """What `load_on_open` found or decided, per detail; `None` is *not loaded*."""

    rows: dict[CatalogueDetail, VariantDetailState]

    def state_of(self, detail: CatalogueDetail) -> VariantDetailState | None:
        return self.rows.get(detail)


def detail_states(db: Session, *, tenant_id: uuid.UUID, model_variant_id: uuid.UUID) -> DetailStates:
    rows = db.scalars(
        select(VariantDetailState)
        .where(VariantDetailState.tenant_id == tenant_id, VariantDetailState.model_variant_id == model_variant_id)
        # Read from the database, not the identity map: another open may
        # have decided a state while this one waited for the lock.
        .execution_options(populate_existing=True)
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


def _fit(value: str | None, length: int) -> str | None:
    """A provider text cut to its column: the screen shows what fits, never
    a 500 for a long remark."""

    if value is None:
        return None
    return value if len(value) <= length else value[:length]


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
    a discontinued one the calendar year its production ended (the closest
    the catalogue row comes to its last model year)."""

    return variant.model_year_to or 0


def _stored_option_year(variant: ModelVariant) -> int:
    """What the stored options stand for: the year asked, or — for the
    provider's "currently available" answer — the current Swiss year. Never
    the first production year of a car still built: its 2021 options are not
    today's."""

    return variant.model_year_to or swiss_today().year


# -- the loaders: one per detail, each replacing this Betrieb's rows -----------------


def _load_colours(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, variant: ModelVariant, fz_key: str, adapter: ProviderAdapter
) -> None:
    try:
        answer = adapter.fetch_variant_with_colours(fz_key)
    except KeyError as exc:
        # The provider no longer lists the key (a retired catalogue row):
        # nothing to load, said so in the state, not a 500 and not a charge.
        raise _NothingToLoad(VARIANT_UNKNOWN_AT_PROVIDER) from exc
    # The same request carries the master data: a revisit fills what is still
    # NULL and records the raw codes (KAN-83), never overwriting a value. In a
    # savepoint: the shared rows it touches (a price year, this year's label,
    # a raw code) may be written by another Betrieb's open at the same
    # moment — then the winner's rows stand and this visit's fill is dropped
    # for this open (both saw the same provider answer; the next visit fills
    # what is still NULL), and this Betrieb's colours still load.
    try:
        with db.begin_nested():
            upsert_model_variant(db, provider_code=provider_code, master=answer.master)
    except IntegrityError:
        logger.info("master data of FzKey %s was visited concurrently; the other visit's rows stand", fz_key)
    kind = answer.master.vehicle_kind_code
    existing = {
        row.colour_code: row
        for row in db.scalars(
            select(ColourCache).where(ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == variant.id)
        )
    }
    seen: set[str] = set()
    for colour in answer.colours:
        code = _fit(colour.colour_code, 64) or colour.colour_code
        if code in seen:
            continue  # one row per code (the table's key); the first listed wins
        seen.add(code)
        description = _fit(colour.description, 160) or code
        # KAN-75: resolved before the row is touched; an unknown code is a
        # gap and leaves `colour_type` NULL (the colour is then not offered).
        colour_type = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_COLOUR_AREA,
            raw_code=colour.colour_area_code,
        )
        row = existing.get(code)
        if row is None:
            row = ColourCache(tenant_id=tenant_id, model_variant_id=variant.id, colour_code=code, description=description)
            db.add(row)
        row.description = description
        row.colour_area_code = _fit(colour.colour_area_code, 16)
        row.colour_type = colour_type
        row.price = colour.price
    for code, row in existing.items():
        if code not in seen:
            db.delete(row)
    db.flush()


def _delete_option(db: Session, option: VariantOption) -> None:
    """An option the provider no longer lists goes with its children: the
    feature links and the raw `SuchCode` rows (`VariantOptionProviderCode`,
    whose key has no cascade)."""

    for link in list(option.equipment_feature_links):
        db.delete(link)
    for raw in db.scalars(
        select(VariantOptionProviderCode).where(VariantOptionProviderCode.variant_option_id == option.id)
    ):
        db.delete(raw)
    db.delete(option)


def _load_options(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, variant: ModelVariant, fz_key: str, adapter: ProviderAdapter
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
        code = _fit(option.option_code, 64) or option.option_code
        if code in rows:
            continue  # one row per key; the first listed wins
        # `group` is a coded field like every other (FR-C-10): resolved
        # through the code map; a miss is a mapping gap and a flat list, and
        # the raw code stays on the row so the resolve fills it at once
        # (`apply_resolved_code_to_option_groups`) — never the provider's text
        # stored as a code.
        option_group = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_OPTION_GROUP,
            raw_code=_fit(option.option_group, 32),
        )
        row = existing.get(code)
        if row is None:
            row = VariantOption(
                tenant_id=tenant_id, model_variant_id=variant.id, option_code=code, description=option.description
            )
            db.add(row)
        row.description = option.description
        row.description_de = option.description_de
        row.description_fr = option.description_fr
        row.description_it = option.description_it
        row.description_en = option.description_en
        row.option_group = option_group
        row.option_group_code = _fit(option.option_group, 32)
        row.price = option.price
        row.is_included = option.is_included
        row.is_package = option.is_package
        row.model_year = model_year
        row.manufacturer_code = _fit(option.manufacturer_code, 64)
        db.flush()  # the id the feature and relation rows reference
        sync_option_equipment_features(
            db, tenant_id=tenant_id, provider_code=provider_code, vehicle_kind_code=kind,
            variant_option=row, feature_codes=option.equipment_feature_codes,
        )
        rows[code] = row
    for code, row in existing.items():
        if code not in rows:
            _delete_option(db, row)
    db.flush()
    # ADR-072: stored and shown, never enforced. A relation to an option the
    # provider did not list for this car names nothing we hold and is skipped;
    # one relation per (from, to, type), whatever the answer repeats.
    written: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    for option in options:
        from_row = rows.get(_fit(option.option_code, 64) or option.option_code)
        if from_row is None:
            continue
        for related in option.pack_option_codes:
            _add_relation(db, tenant_id=tenant_id, variant=variant, model_year=model_year, from_row=from_row,
                          to_row=rows.get(related), relation_type=_RELATION_CONTAINS, price=None, written=written)
        for related in option.excluded_option_codes:
            _add_relation(db, tenant_id=tenant_id, variant=variant, model_year=model_year, from_row=from_row,
                          to_row=rows.get(related), relation_type=_RELATION_EXCLUDES, price=None, written=written)
        for priced in option.priced_with:
            _add_relation(db, tenant_id=tenant_id, variant=variant, model_year=model_year, from_row=from_row,
                          to_row=rows.get(priced.option_code), relation_type=_RELATION_PRICED_WITH,
                          price=priced.price, written=written)
    db.flush()


def _add_relation(
    db: Session, *, tenant_id: uuid.UUID, variant: ModelVariant, model_year: int, from_row: VariantOption,
    to_row: VariantOption | None, relation_type: str, price, written: set[tuple[uuid.UUID, uuid.UUID, str]],
) -> None:
    if to_row is None or to_row.id == from_row.id:
        return
    key = (from_row.id, to_row.id, relation_type)
    if key in written:
        return
    written.add(key)
    db.add(
        VariantOptionRelation(
            tenant_id=tenant_id, model_variant_id=variant.id, model_year=model_year, from_option_id=from_row.id,
            to_option_id=to_row.id, relation_type=relation_type, price_in_combination=price,
        )
    )


def _load_tyre_specs(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, variant: ModelVariant, fz_key: str, adapter: ProviderAdapter
) -> None:
    number = _type_approval_number(variant)
    tyres = adapter.fetch_tyre_specs(type_approval_number=number) if number else []
    kind = _raw_vehicle_kind(db, variant=variant, provider_code=provider_code)
    existing = {
        (row.axle_code, row.season_code, row.size, row.rims): row
        for row in db.scalars(
            select(TyreSpecCache).where(
                TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == variant.id
            )
        )
    }
    seen: set[tuple[str | None, str | None, str, str | None]] = set()
    for tyre in tyres:
        size = _fit(tyre.size, 32) or tyre.size
        rims = _fit(tyre.rims, 32)
        key = (_fit(tyre.axle_code, 16), _fit(tyre.season_code, 16), size, rims)
        if key in seen:
            # One row per approved wheel (KAN-596, ADR-141: axle, season,
            # dimension, rim). Rows differing by gearbox alone name the same
            # wheel; the first one listed is kept.
            continue
        seen.add(key)
        # Resolved before the row is touched: the lookup may flush, and a new
        # row must not reach the database half-filled (KAN-75's own order).
        axle = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_AXLE_POSITION, raw_code=key[0]
        )
        season = resolve_colour_or_tyre_code(
            db, provider_code=provider_code, vehicle_kind_code=kind, code_group=_TYRE_TYPE, raw_code=key[1]
        )
        row = existing.get(key)
        if row is None:
            row = TyreSpecCache(
                tenant_id=tenant_id, model_variant_id=variant.id, axle_code=key[0], season_code=key[1], size=size,
                rims=rims,
            )
            db.add(row)
        row.axle = axle
        row.season = season
        row.load_index = _fit(tyre.load_index, 8)
        row.speed_rating = _fit(tyre.speed_rating, 4)
        row.remark = _fit(tyre.remark, 160)
        row.rim_width = tyre.rim_width if tyre.rim_width is not None and abs(tyre.rim_width) < 1000 else None
        row.rim_size = tyre.rim_size
        row.rim_offset = tyre.rim_offset
        row.is_standard = tyre.is_standard
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
    db.flush()


def _load_images(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, variant: ModelVariant, fz_key: str, adapter: ProviderAdapter
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
        key = _fit(image.image_key, 160) or image.image_key
        if key in seen:
            continue
        if image.image_url is not None and len(image.image_url) > 500:
            # A cut URL is a broken picture: left out, and said so.
            logger.warning("image %s of FzKey %s left out: its URL is longer than 500 characters", key, fz_key)
            continue
        seen.add(key)
        bild_typ = _fit(image.bild_typ, 16) or "?"
        bild_art = _fit(image.bild_art, 16) or "?"
        row = existing.get(key)
        if row is None:
            row = ImageRef(
                tenant_id=tenant_id, model_variant_id=variant.id, image_key=key, bild_typ=bild_typ, bild_art=bild_art
            )
            db.add(row)
        row.bild_typ = bild_typ
        row.bild_art = bild_art
        row.sequence = image.sequence
        row.image_url = image.image_url
    for key, row in existing.items():
        if key not in seen:
            db.delete(row)
    db.flush()


def _load_energy_label(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, variant: ModelVariant, fz_key: str, adapter: ProviderAdapter
) -> None:
    """The provider's A–G class per calculation year, stored once for every
    Betrieb (ADR-042: a value as of a year) with `source = auto_i_dat`,
    filling an absence only — a BFE row, or any row already held for the
    year, is left alone. The provider answers one row per gearbox: a year
    whose rows disagree on the class is not stored (the Energieetikette is a
    legal display; a guess shared by every Betrieb is worse than none) and
    the state says so. A letter the `energy_efficiency_category` list does
    not know is left out."""

    number = _type_approval_number(variant)
    labels = adapter.fetch_energy_label(type_approval_number=number) if number else []
    if not labels:
        return
    known = get_active_reference_value_codes(db, _ENERGY_LABEL_LIST) or set()
    by_year: dict[int, set[str]] = {}
    for label in labels:
        code = label.energy_class.strip().lower()
        if code in known:
            by_year.setdefault(label.calculation_year, set()).add(code)
    ambiguous = False
    for year, codes in by_year.items():
        if len(codes) != 1:
            ambiguous = True
            logger.info(
                "energy label of variant %s for %d: gearboxes disagree (%s); not stored", variant.id, year, sorted(codes)
            )
            continue
        held = db.scalar(
            select(ModelVariantEnergyRating.id).where(
                ModelVariantEnergyRating.model_variant_id == variant.id, ModelVariantEnergyRating.rating_year == year
            )
        )
        if held is not None:
            continue
        # The table is shared by every Betrieb: two opens of the same car at
        # once may both find the year absent; the second insert loses on the
        # unique key and the first one's row stands — in a savepoint of its
        # own, so the years stored before it in this open stay.
        try:
            with db.begin_nested():
                db.add(
                    ModelVariantEnergyRating(
                        model_variant_id=variant.id, rating_year=year, energy_efficiency_category=next(iter(codes)),
                        source=_ENERGY_LABEL_SOURCE,
                    )
                )
                db.flush()
        except IntegrityError:
            pass
    # KAN-597 (ADR-142): every gearbox's own label, class and PDF link, beside
    # the one-class-per-year rating above — stored even for a year whose
    # gearboxes disagree, since that is exactly when the seller needs both.
    _store_energy_labels(db, variant=variant, labels=labels, known=known)
    if ambiguous:
        raise _LoadedWithNote(ENERGY_LABEL_AMBIGUOUS)


def _store_energy_labels(db: Session, *, variant: ModelVariant, labels: list, known: set[str]) -> None:
    """One `ModelVariantEnergyLabel` per (year, gearbox) the provider sent,
    under a class the list knows; a row already held is kept (fill-if-absent,
    ADR-122 (3)) and only gains a link it lacked. The first row listed for a
    (year, gearbox) wins. Each insert in its own savepoint: the table is
    shared by every Betrieb, and a concurrent open's row stands."""

    seen: set[tuple[int, str]] = set()
    for label in labels:
        code = label.energy_class.strip().lower()
        if code not in known:
            continue
        gearbox = _fit(label.gearbox, 64) or ""
        key = (label.calculation_year, gearbox)
        if key in seen:
            continue
        seen.add(key)
        url = label.label_url if label.label_url is not None and len(label.label_url) <= 500 else None
        if label.label_url is not None and url is None:
            logger.warning("energy label link of variant %s left out: longer than 500 characters", variant.id)
        held = db.scalar(
            select(ModelVariantEnergyLabel).where(
                ModelVariantEnergyLabel.model_variant_id == variant.id,
                ModelVariantEnergyLabel.label_year == key[0],
                ModelVariantEnergyLabel.gearbox == gearbox,
            )
        )
        if held is not None:
            if held.label_url is None and url is not None:
                held.label_url = url
                db.flush()
            continue
        try:
            with db.begin_nested():
                db.add(
                    ModelVariantEnergyLabel(
                        model_variant_id=variant.id, label_year=key[0], gearbox=gearbox,
                        energy_efficiency_category=code, label_url=url,
                    )
                )
                db.flush()
        except IntegrityError:
            pass


_Loader = Callable[..., None]
_LOADERS: Final[dict[CatalogueDetail, _Loader]] = {
    CatalogueDetail.COLOURS: _load_colours,
    CatalogueDetail.OPTIONS: _load_options,
    CatalogueDetail.TYRE_SPECS: _load_tyre_specs,
    CatalogueDetail.IMAGES: _load_images,
    CatalogueDetail.ENERGY_LABEL: _load_energy_label,
}


# -- the lock: one open at a time per (Betrieb, variant) ------------------------------------

# How long a second open waits for the first one's current detail before it
# gives up and leaves the rest to that open (the provider's own timeout is
# 10 s per attempt, one retry).
LOCK_WAIT: Final = "25s"
# Postgres: `lock_not_available`, what `lock_timeout` raises.
_LOCK_NOT_AVAILABLE: Final = "55P03"


def _lock_keys(tenant_id: uuid.UUID, variant_id: uuid.UUID) -> tuple[int, int]:
    """Two signed 32-bit keys for `pg_advisory_xact_lock(int, int)`, from the
    two ids' low words. A collision only serialises two opens."""

    def key(value: uuid.UUID) -> int:
        number = value.int & 0xFFFFFFFF
        return number - 0x100000000 if number >= 0x80000000 else number

    return key(tenant_id), key(variant_id)


def _lock_for_detail(db: Session, *, tenant_id: uuid.UUID, variant_id: uuid.UUID) -> bool:
    """Takes the (Betrieb, variant) advisory lock for the transaction of one
    detail — on the session's own connection, released by that detail's
    commit, so a request never holds a second pooled connection and a stuck
    provider never pins one. Bounded by `LOCK_WAIT`: `False` means another
    open of the same car still holds it after that long; the caller leaves
    the remaining details to that open. `SET LOCAL lock_timeout` bounds
    every lock wait of the detail's transaction, a row lock inside the
    gateway block included — a wait past it is that detail's failure, never
    a hang. The SQLite lane has no advisory locks and never counts as
    verification."""

    if db.get_bind().dialect.name != "postgresql":
        return True
    first, second = _lock_keys(tenant_id, variant_id)
    try:
        db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_WAIT}'"))
        db.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"), {"a": first, "b": second})
    except OperationalError as exc:
        if getattr(getattr(exc, "orig", None), "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise  # a dropped connection or a statement timeout is not "someone holds the lock"
        db.rollback()
        logger.info("catalogue details of variant %s: another open holds the lock past %s", variant_id, LOCK_WAIT)
        return False
    return True


# -- the two entry points -------------------------------------------------------------


def _wanted(
    db: Session, *, connection: IntegrationConnection, states: DetailStates, force: bool
) -> list[CatalogueDetail]:
    wanted: list[CatalogueDetail] = []
    for detail in DETAILS:
        row = states.state_of(detail)
        if force or row is None or row.state == DetailState.UNAVAILABLE:
            wanted.append(detail)
        elif row.state == DetailState.NOT_IN_CONTRACT:
            # Asked again once a later «Test connection» recorded the option as
            # granted (a licence upgrade); otherwise never on open — a billed
            # 403 per open is not a retry.
            entitlement = get_entitlement(db, connection_id=connection.id, capability_code=DETAIL_CAPABILITY[detail])
            if entitlement is not None and entitlement.granted and entitlement.checked_at > row.loaded_at:
                wanted.append(detail)
    return wanted


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
    detail is its own gateway block, savepoint and commit; a refused or
    failed one ends in its state and the loop goes on. Without a connection,
    or for a variant unknown at the provider, nothing is asked and nothing is
    written. A failure that is no provider failure (a bug) propagates after
    the details before it were committed."""

    states = detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return states
    connection, provider_code = found
    fz_key = provider_key_of(db, variant=variant, provider_code=provider_code)
    if fz_key is None:
        return states
    if not _wanted(db, connection=connection, states=states, force=force):
        return states
    rows = states.rows
    for detail in _wanted(db, connection=connection, states=states, force=force):
        if not _lock_for_detail(db, tenant_id=tenant_id, variant_id=variant.id):
            break
        # Judged again under the lock: the open that held it before us may
        # have decided this detail (read from the database, not the
        # identity map — `detail_states` populates existing rows).
        states = detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
        rows = states.rows
        if detail not in _wanted(db, connection=connection, states=states, force=force):
            db.commit()
            continue
        _load_one(
            db, tenant_id=tenant_id, connection=connection, provider_code=provider_code, variant=variant,
            fz_key=fz_key, detail=detail, actor_id=actor_id, purpose=purpose, rows=rows,
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
    note: str | None = None
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
            try:
                _LOADERS[detail](
                    db, tenant_id=tenant_id, provider_code=provider_code, variant=variant, fz_key=fz_key,
                    adapter=adapter,
                )
            except (_LoadedWithNote, _NothingToLoad) as exc:
                # Caught inside the gateway block: an answer with nothing in
                # it is the provider's coherent answer, not a failure to
                # charge the breaker with.
                note = exc.note
    except ProviderNotEntitledError as exc:
        set_state(DetailState.NOT_IN_CONTRACT, type(exc).__name__)
        return
    except ProviderRequestError as exc:
        # The provider's coherent "no" to our request (400/422): asking again
        # on the next open would buy the same answer; `present` with the
        # note, until «Neu laden».
        set_state(DetailState.PRESENT, type(exc).__name__)
        return
    except ProviderGatewayError as exc:
        # A transport failure, an open breaker, a refused login, a connection
        # not configured for a call with nobody to enrol: asked again on the
        # next open. The class name only — a provider's message can echo
        # what it was sent.
        logger.info("catalogue detail %s of variant %s unavailable: %s", detail.value, variant.id, type(exc).__name__)
        set_state(DetailState.UNAVAILABLE, type(exc).__name__)
        return
    set_state(DetailState.PRESENT, note)


def reload_details(
    db: Session, *, tenant_id: uuid.UUID, variant: ModelVariant, actor_id: uuid.UUID, reason: str | None = None
) -> DetailStates:
    """«Neu laden» (ticket point 4): one fresh request per detail, replacing
    this Betrieb's stored rows, recorded in the audit log with the actor
    before the first request leaves — the record says a reload was asked for
    and by whom, whatever each detail then ends in (its state says that).
    Nowhere else does anything refresh a detail. Without a connection, or for
    a variant with no key at the provider, there is nothing to reload and
    nothing is recorded."""

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None or provider_key_of(db, variant=variant, provider_code=found[1]) is None:
        return detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id)
    before = {
        detail.value: (row.state.value, row.loaded_at.isoformat())
        for detail, row in detail_states(db, tenant_id=tenant_id, model_variant_id=variant.id).rows.items()
    }
    record_audit_event(
        db,
        entity_type="vehicle_variant_details",
        entity_id=variant.id,
        tenant_id=tenant_id,
        action="reload",
        actor_id=actor_id,
        before=before,
        after=None,
        reason=reason or "KAN-211: «Neu laden» on the stock car's technical data",
    )
    db.commit()
    return load_on_open(
        db, tenant_id=tenant_id, variant=variant, actor_id=actor_id, purpose="catalogue_details_reload", force=True
    )
