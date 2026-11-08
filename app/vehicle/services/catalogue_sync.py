"""Per-tenant catalogue mirror sync (WP-6 PR-4) — the real work the
provider-gateway mock/real adapters exist to feed. `app.integration.public
.call_capability` is the only way this module ever reaches a provider;
this file resolves the connection itself via `get_enabled_connection`
(never handed one by a caller), so `app.integration` never needs to know
anything about vehicle's own tables — the same seam
`get_enabled_connection`'s own docstring describes.

No bulk retrieval, for any provider (ADR-023 as amended 2026-10-29; KAN-485).
Anto's ruling: the auto-i-dat agreement permits single lookups only — never
retrieving the whole catalogue, storing it or passing it on — and the demo
provider follows the same rule so that nothing built on it assumes a full
mirror. The full seed, the daily `FzKeyChanged` delta, its 3-month fallback
reseed, the daily job that ran them and the sync-age alarm (A-12) built on
their `System` watermark are gone; no adapter offers `FzKeyChanged` any more
(`tests/architecture/test_no_bulk_catalogue_retrieval.py`). A variant reaches
the catalogue only through a single lookup: identification's best match
(`identification.py`, master data only; its plate lookup fills the plate-lookup
cache, not the catalogue) or `sync_catalogue_variant` below (master data plus
this tenant's options, colours, tyres and images; nothing calls it yet —
KAN-211).
The bookkeeping table those runs wrote (`vehicle_provider_sync_state`) and
the platform's sync-status board that read it were retired by KAN-497 (Anto,
2026-10-31, option A).

Provider data a lookup stored is kept indefinitely (Anto, 2026-10-29, KAN-487:
paid for once): there is no retention window on any of these tables.

One lookup is one `call_capability` block and savepoint (KAN-78's per-key
isolation): a provider failure — a Datenname the account is refused, a
transport error — is logged by the gateway, charged to the connection's
breaker, rolls this variant's writes back and comes back as a `SkippedFzKey`.
Only a `ProviderGatewayError` raised *by a call* is handled this way; anything
else is a bug and propagates. The gateway logs each provider request the block
makes as its own call-log row (KAN-486), so one lookup shows up to five rows
(`Fahrzeuge`, `Optionen`, `OptionenFarben`, `PneuDimTS`, `Bilder`).
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.integration.public import (
    IntegrationConnection,
    ProviderGatewayError,
    VariantMasterData,
    call_capability,
    get_enabled_connection,
)
from app.platform.public import get_active_reference_value_codes
from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    TypeApproval,
    VariantOption,
    VariantOptionEquipmentFeature,
    VariantPrice,
    VariantTypeApproval,
)
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, TyreSpecCache
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.provider import ProviderEntityRef, VariantOptionProviderCode, VariantProviderCode
from app.vehicle.models.spec_block import SPEC_BLOCK_FIELDS
from app.vehicle.services.plate import swiss_today
from app.vehicle.services.provider import resolve_provider_code

logger = logging.getLogger("app.vehicle.catalogue_sync")

# Mock and real are separate provider_codes / separate connections, never
# a runtime flag (rule 7's own posture) — a tenant's own vehicle-data
# connection is whichever of these two it actually has enabled; trying
# both here is this module's own resolution step, not a flag on one
# connection.
VEHICLE_DATA_PROVIDER_CODES = ("auto_i_dat", "auto_i_dat_mock")

_ENTITY_TYPE_MODEL_VARIANT = "model_variant"

# The variant fields resolved through `ProviderCodeMap`, each with the
# `VariantMasterData` attribute its raw code arrives in. A field's
# `code_group` is its own column name (see `ProviderCodeMap`'s docstring).
# KAN-547 added the spec block's four reference fields: the identAPI sends
# `category`, `euronorm` and `consumptionNorm` as codes, and the motorcycle's
# `drive` is a stroke count (`engine_cycle`), routed below — never into
# `drivetrain` (PRD risk R-C-5; KAN-447's open question, answered by the
# identAPI's unambiguous `type`).
_CODED_FIELDS: Final = {
    "vehicle_kind": "vehicle_kind_code",
    "fuel_type": "fuel_type_code",
    "body_style": "body_style_code",
    "drivetrain": "drivetrain_code",
    "engine_cycle": "drivetrain_code",
    "transmission": "transmission_code",
    "vehicle_class": "vehicle_class_code",
    "emission_standard": "emission_standard_code",
    "consumption_norm": "consumption_norm_code",
    "valuation_classification": "valuation_classification_code",
}
# The canonical vehicle kind whose `drive` is a stroke count (R-C-5). Routing
# reads the **resolved** kind, never a raw code (the reading rule).
_MOTORCYCLE: Final = "motorcycle"
# The energy label the identAPI sends is stored per year (ADR-042) under the
# `energy_efficiency_category` reference list's own codes; a label that list
# does not know is left out (the list is platform's; `platform.public`).
_ENERGY_LABEL_SOURCE: Final = "auto_i_dat"
_ENERGY_LABEL_LIST: Final = "energy_efficiency_category"
# The option attribute resolved through `ProviderCodeMap` (`SuchCode`), many
# codes per option; its raw codes are kept per option (KAN-250).
_EQUIPMENT_FEATURE: Final = "equipment_feature"
# The colour and tyre attributes resolved through `ProviderCodeMap` (KAN-75),
# one code each; the raw code is kept on the cached row, so a resolved gap
# fills it at once (`apply_resolved_code_to_colours_and_tyres`).
_COLOUR_AREA: Final = "colour_area"  # FarbArt
_AXLE_POSITION: Final = "axle_position"  # AchsenCode
_TYRE_TYPE: Final = "tyre_type"  # PneuTyp
COLOUR_AND_TYRE_CODE_GROUPS: Final = (_COLOUR_AREA, _AXLE_POSITION, _TYRE_TYPE)
# Master-data fields a later visit fills while they are NULL (KAN-83): the
# whole specification block the adapter carries by the same names (KAN-547 —
# before it, three fields), plus the production range's end. The coded
# reference fields are resolved separately (`_CODED_FIELDS`); `name` and
# `model_year_from` are NOT NULL, so there is never a gap to fill.
_FILLABLE_MASTER_FIELDS: Final = (
    "model_year_to",
    *(field for field in SPEC_BLOCK_FIELDS if field not in _CODED_FIELDS),
)


class NoVehicleDataConnectionError(Exception):
    """`sync_catalogue_variant` for a tenant with no enabled vehicle-data
    connection — a lookup with no contract to make it under. Reads never
    raise it (PR-5's "fully usable module without a provider contract").
    """

    def __init__(self, tenant_id: uuid.UUID) -> None:
        super().__init__(f"Tenant {tenant_id} has no enabled vehicle-data connection.")


SkippedField = Literal["master_data", "options", "colours", "tyre_specs", "images"]


@dataclass(frozen=True)
class SkippedFzKey:
    """A variant `sync_catalogue_variant` did not sync in full. `field` is
    the call it failed on; `error` is the exception's class name, never its
    message.
    """

    fz_key: str
    field: SkippedField
    error: str


@dataclass
class _KeyProgress:
    """Which provider call the current FzKey is on, so a failure can name it."""

    field: SkippedField = "master_data"
    # Set as the first statement inside the `call_capability` block: a
    # failure while it is still False came from resolving the adapter, not
    # from a call about this key.
    entered: bool = False


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def find_enabled_vehicle_data_connection(
    db: Session, *, tenant_id: uuid.UUID
) -> tuple[IntegrationConnection, str] | None:
    """Tries each vehicle-data provider_code in turn — a tenant has at
    most one enabled connection among them in practice, but nothing here
    assumes that; the first enabled hit wins.
    """

    for provider_code in VEHICLE_DATA_PROVIDER_CODES:
        connection = get_enabled_connection(db, tenant_id=tenant_id, provider_code=provider_code)
        if connection is not None:
            return connection, provider_code
    return None


def _upsert_brand(db: Session, *, display_name: str) -> Brand:
    code = _slugify(display_name)
    brand = db.scalar(select(Brand).where(Brand.code == code))
    if brand is None:
        brand = Brand(code=code, display_name=display_name)
        db.add(brand)
        db.flush()
    return brand


def _upsert_model_group(db: Session, *, brand: Brand, name: str) -> ModelGroup:
    group = db.scalar(select(ModelGroup).where(ModelGroup.brand_id == brand.id, ModelGroup.name == name))
    if group is None:
        group = ModelGroup(brand_id=brand.id, name=name)
        db.add(group)
        db.flush()
    return group


def _resolve_code(
    db: Session, *, provider_code: str, vehicle_kind_qualifier: str, code_group: str, provider_value: str | None
) -> str | None:
    if not provider_value or not vehicle_kind_qualifier:
        # No code, or a row without `type`: a code read under no vehicle kind cannot be mapped
        # (the vocabulary is per kind) and is no gap either — no value, no gap.
        return None
    canonical = resolve_provider_code(
        db, provider=provider_code, vehicle_kind=vehicle_kind_qualifier, code_group=code_group,
        provider_code=provider_value,
    )
    return canonical.value_code if canonical is not None else None


def store_seen_variants(db: Session, *, provider_code: str, masters: list[VariantMasterData]) -> list[ModelVariant]:
    """Every variant a lookup or a browse page returned, through
    `upsert_model_variant` (master data only, ADR-075; fill-if-NULL, KAN-83),
    committed — the stored rows are a cache of what was seen, never the
    source (ADR-112). Returned in `masters` order.

    Each upsert runs in a savepoint: two Betriebe can see one new variant (or
    one new brand) at the same moment, and the loser's unique violation is
    answered by re-reading the row the winner wrote — a lookup never fails
    because another dealer saw the same car first (KAN-547)."""

    variants: list[ModelVariant] = []
    for master in masters:
        try:
            with db.begin_nested():
                variant = upsert_model_variant(db, provider_code=provider_code, master=master)
        except IntegrityError:
            logger.info("variant %s was stored concurrently; re-reading", master.fz_key)
            variant = upsert_model_variant(db, provider_code=provider_code, master=master)
        variants.append(variant)
    if variants:
        db.commit()
    return variants


def find_variant_by_fz_key(db: Session, *, provider_code: str, fz_key: str) -> ModelVariant | None:
    ref = db.scalar(
        select(ProviderEntityRef).where(
            ProviderEntityRef.entity_type == _ENTITY_TYPE_MODEL_VARIANT,
            ProviderEntityRef.provider == provider_code,
            ProviderEntityRef.provider_key == fz_key,
        )
    )
    return db.get(ModelVariant, ref.entity_id) if ref is not None else None


def upsert_model_variant(db: Session, *, provider_code: str, master: VariantMasterData) -> ModelVariant:
    """Keyed by `ProviderEntityRef(entity_type="model_variant", provider,
    provider_key=fz_key)` — the natural idempotency key for a repeated lookup
    of an already-known variant, so a variant that
    hasn't changed at the provider is never duplicated locally.
    `Brand`/`ModelGroup` identity is resolved by slugified display name
    (a deterministic, provider-code-free key — Brand is not a
    reference_value and carries no provider-mapping table of its own,
    per catalogue.py's own "too high-cardinality" reasoning) rather than
    through the mapping-gap machinery. `*_code` fields ARE resolved
    through that machinery (WP-5 PR-2); an unresolved code is left `None`
    on the variant and a MappingGap row is written for PR-8's
    already-shipped admin queue — never silently dropped. Resolving the
    `vehicle_kind` code_group itself uses the raw provider vehicle-kind
    code as its own qualifier (a documented, self-referential admin
    mapping — vehicle_kind is the one code_group ProviderCodeMap's own
    qualifier column can't meaningfully disambiguate against anything
    else).

    Every visit records the variant's raw codes (`VariantProviderCode`), and
    a visit of an existing variant fills the fields that are still NULL —
    a code mapped since, a price the provider has started to send (KAN-83).
    A field that holds a value is never overwritten here.
    """

    existing = find_variant_by_fz_key(db, provider_code=provider_code, fz_key=master.fz_key)
    if existing is not None:
        canonical_kind = existing.vehicle_kind or _resolve_kind(db, provider_code=provider_code, master=master)
        _record_raw_codes(db, variant=existing, provider_code=provider_code, master=master, canonical_kind=canonical_kind)
        _fill_null_fields(db, variant=existing, provider_code=provider_code, master=master, canonical_kind=canonical_kind)
        _link_type_approvals(db, variant=existing, type_approval_numbers=master.type_approval_numbers)
        _store_catalogue_prices(db, variant=existing, master=master)
        _store_energy_label(db, variant=existing, master=master)
        return existing

    brand = _upsert_brand(db, display_name=master.brand_display_name)
    group = _upsert_model_group(db, brand=brand, name=master.model_group_name)

    # KAN-547: the whole specification block (ADR-071) the adapter carries by
    # the column's own name — every `_FILLABLE_MASTER_FIELDS` entry — plus
    # every coded field resolved through the code map (an unresolved code is
    # left NULL and recorded as a mapping gap, never a guess).
    resolved = _resolve_codes(db, provider_code=provider_code, master=master)
    variant = ModelVariant(
        model_group_id=group.id,
        name=master.variant_name,
        model_year_from=master.model_year_from,
        **{field_name: getattr(master, field_name) for field_name in _FILLABLE_MASTER_FIELDS},
        **resolved,
    )
    db.add(variant)
    db.flush()
    db.add(
        ProviderEntityRef(
            entity_type=_ENTITY_TYPE_MODEL_VARIANT, entity_id=variant.id, provider=provider_code,
            provider_key=master.fz_key,
        )
    )
    db.flush()
    _record_raw_codes(
        db, variant=variant, provider_code=provider_code, master=master, canonical_kind=resolved["vehicle_kind"]
    )
    _link_type_approvals(db, variant=variant, type_approval_numbers=master.type_approval_numbers)
    _store_catalogue_prices(db, variant=variant, master=master)
    _store_energy_label(db, variant=variant, master=master)
    return variant


def _raw_codes(master: VariantMasterData, *, canonical_kind: str | None) -> dict[str, str | None]:
    """The raw code per coded field, routed by the **resolved** vehicle kind
    (never a raw code, KAN-447): a motorcycle's `drive` is its `engine_cycle`
    and it has no `drivetrain`; every other kind has a `drivetrain` and no
    `engine_cycle`."""

    codes = {code_group: getattr(master, attribute) for code_group, attribute in _CODED_FIELDS.items()}
    if canonical_kind == _MOTORCYCLE:
        codes["drivetrain"] = None
    else:
        # A car, a light commercial — or a kind still unmapped, where `drive`
        # is recorded as the drivetrain it is for every kind but motorcycles
        # (as before KAN-547); resolving the kind's gap and revisiting a
        # motorcycle moves it.
        codes["engine_cycle"] = None
    return codes


def _resolve_kind(db: Session, *, provider_code: str, master: VariantMasterData) -> str | None:
    return _resolve_code(
        db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
        code_group="vehicle_kind", provider_value=master.vehicle_kind_code,
    )


def _resolve_codes(db: Session, *, provider_code: str, master: VariantMasterData) -> dict[str, str | None]:
    """Every coded field's canonical value (or None, with its gap written)."""

    if not master.vehicle_kind_code:
        logger.warning(
            "variant %s arrived without a vehicle kind: its coded fields stay NULL and file no gap", master.fz_key
        )
    canonical_kind = _resolve_kind(db, provider_code=provider_code, master=master)
    resolved: dict[str, str | None] = {"vehicle_kind": canonical_kind}
    for code_group, raw_code in _raw_codes(master, canonical_kind=canonical_kind).items():
        if code_group == "vehicle_kind":
            continue
        resolved[code_group] = _resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group=code_group, provider_value=raw_code,
        )
    return resolved


def _record_raw_codes(
    db: Session, *, variant: ModelVariant, provider_code: str, master: VariantMasterData, canonical_kind: str | None
) -> None:
    """Keeps one `VariantProviderCode` row per coded field in step with what
    the provider sent this time: a new code is written, a changed one
    updated, and one the provider no longer sends removed.
    """

    rows = {
        row.code_group: row
        for row in db.scalars(
            select(VariantProviderCode).where(
                VariantProviderCode.model_variant_id == variant.id, VariantProviderCode.provider == provider_code
            )
        )
    }
    for code_group, raw_code in _raw_codes(master, canonical_kind=canonical_kind).items():
        row = rows.get(code_group)
        if raw_code is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(
                VariantProviderCode(
                    model_variant_id=variant.id, provider=provider_code, vehicle_kind=master.vehicle_kind_code,
                    code_group=code_group, provider_code=raw_code,
                )
            )
        elif (row.vehicle_kind, row.provider_code) != (master.vehicle_kind_code, raw_code):
            row.vehicle_kind = master.vehicle_kind_code
            row.provider_code = raw_code
    db.flush()


def _fill_null_fields(
    db: Session, *, variant: ModelVariant, provider_code: str, master: VariantMasterData, canonical_kind: str | None
) -> None:
    """Fill-if-NULL, never overwrite (KAN-83 exit criterion 1): the variant
    is one global row (ADR-075), and replacing a value it already holds is
    the explicit refresh's job, not a sync side effect. A code that still
    does not resolve leaves the field NULL and touches its gap again, as on
    creation.

    Every code is resolved first — which may write its `MappingGap` — and
    only then is the variant written, by one conditional UPDATE: the same
    gap-then-variant lock order as a resolve (`resolve_mapping_gap` locks
    the gap, `apply_resolved_code_to_variants` then the variants), so the
    two cannot deadlock; and the `IS NULL` test and the version bump happen
    in the database, so a field a resolve filled meanwhile is kept and the
    version moves once per actual change.
    """

    fills: dict[str, object] = {}
    for field_name in _FILLABLE_MASTER_FIELDS:
        value = getattr(master, field_name)
        if getattr(variant, field_name) is None and value is not None:
            fills[field_name] = value
    for code_group, raw_code in _raw_codes(master, canonical_kind=canonical_kind).items():
        if getattr(variant, code_group) is not None or raw_code is None:
            continue
        if code_group == "vehicle_kind":
            canonical = canonical_kind  # resolved once by the caller, its gap bumped once
        else:
            canonical = _resolve_code(
                db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
                code_group=code_group, provider_value=raw_code,
            )
        if canonical is not None:
            fills[code_group] = canonical
    if not fills:
        return

    columns = {name: getattr(ModelVariant, name) for name in fills}
    db.execute(
        update(ModelVariant)
        .where(ModelVariant.id == variant.id, or_(*(column.is_(None) for column in columns.values())))
        .values(
            {
                **{name: func.coalesce(column, fills[name]) for name, column in columns.items()},
                "version": ModelVariant.version + 1,
            }
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(variant)


def _store_catalogue_prices(db: Session, *, variant: ModelVariant, master: VariantMasterData) -> None:
    """`catalogPrices` → `vehicle_variant_price`, one row per model year
    (KAN-547). Fill-if-absent: a year the table already holds — a provider
    row from an earlier visit, or a price an advisor typed — is never
    overwritten by a lookup (the KAN-83 rule for master data)."""

    if not master.catalogue_prices:
        return
    held = set(
        db.scalars(select(VariantPrice.model_year).where(VariantPrice.model_variant_id == variant.id)).all()
    )
    for price in master.catalogue_prices:
        if price.year in held:
            continue
        held.add(price.year)
        db.add(
            VariantPrice(
                model_variant_id=variant.id, model_year=price.year, new_price=price.price,
                price_is_net=bool(master.price_is_net), source="provider",
            )
        )
    db.flush()


def _store_energy_label(db: Session, *, variant: ModelVariant, master: VariantMasterData) -> None:
    """The identAPI's current A–G label, stored as this year's rating
    (ADR-042: a label is a fact of a year, never a flat property) with
    `source = auto_i_dat` — Anto, 2026-11-07 (KAN-547 decision 4). The value
    is the `energy_efficiency_category` list's own code for the letter; a
    letter the list does not carry is left out. An official BFE row for the
    year, or any row already held for it, is left alone: the provider's label
    fills an absence, it never overwrites."""

    label = (master.energy_label or "").strip().lower()
    if not label or label not in (get_active_reference_value_codes(db, _ENERGY_LABEL_LIST) or set()):
        return
    year = swiss_today().year
    held = db.scalar(
        select(ModelVariantEnergyRating.id).where(
            ModelVariantEnergyRating.model_variant_id == variant.id, ModelVariantEnergyRating.rating_year == year
        )
    )
    if held is not None:
        return
    db.add(
        ModelVariantEnergyRating(
            model_variant_id=variant.id, rating_year=year, energy_efficiency_category=label,
            source=_ENERGY_LABEL_SOURCE,
        )
    )
    db.flush()


def apply_resolved_code_to_variants(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str, value_code: str,
) -> int:
    """Fills `value_code` into every variant whose `code_group` field is
    NULL and whose recorded raw code is exactly this (provider, vehicle
    kind, code group, code) — the variants a just-resolved mapping gap had
    been blocking (KAN-83, FR-C-10). A variant that already holds a value
    is left as it is. Code groups that are not variant fields (an equipment
    feature, a colour area) fill nothing here. Returns how many were
    filled.

    One conditional UPDATE: the NULL test runs in the database under the
    row lock, so a variant a concurrent sync has just filled is skipped,
    and each filled variant's version moves exactly once.
    """

    return len(
        fill_variants_for_resolved_code(
            db, provider=provider, vehicle_kind=vehicle_kind, code_group=code_group, provider_code=provider_code,
            value_code=value_code,
        )
    )


def fill_variants_for_resolved_code(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str, value_code: str,
) -> list[uuid.UUID]:
    """`apply_resolved_code_to_variants`, returning the ids of the variants
    it filled — what the configuration heal (KAN-240) starts from."""

    if code_group not in _CODED_FIELDS:
        return []
    field_column = getattr(ModelVariant, code_group)
    blocked = select(VariantProviderCode.model_variant_id).where(
        VariantProviderCode.provider == provider,
        VariantProviderCode.vehicle_kind == vehicle_kind,
        VariantProviderCode.code_group == code_group,
        VariantProviderCode.provider_code == provider_code,
    )
    filled = db.scalars(
        update(ModelVariant)
        .where(ModelVariant.id.in_(blocked), field_column.is_(None))
        .values({code_group: value_code, "version": ModelVariant.version + 1})
        .returning(ModelVariant.id)
        .execution_options(synchronize_session="fetch")
    ).all()
    db.flush()
    return list(filled)


def apply_resolved_code_to_options(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str, value_code: str,
) -> int:
    """Adds the `value_code` equipment feature to every option, in every
    tenant, whose recorded raw `SuchCode` list contains exactly this
    (provider, vehicle kind, code) — the options a just-resolved
    `equipment_feature` gap had been blocking (KAN-250, FR-C-10). Each row
    is written with its option's own `tenant_id` (ADR-013); an option that
    already carries the feature is left as it is. Any other code group adds
    nothing. Returns how many options gained the feature.

    Runs in the resolve's transaction, after `resolve_mapping_gap` has
    locked the gap. A sync visit of the same option resolves the code (and
    so bumps the gap) before it reads the option's features, so it either
    waits for this resolve and then sees the feature, or committed before it
    and this resolve sees its raw codes — the feature is never written twice.
    """

    if code_group != _EQUIPMENT_FEATURE:
        return 0
    blocked = db.execute(
        select(VariantOptionProviderCode.tenant_id, VariantOptionProviderCode.variant_option_id)
        .where(
            VariantOptionProviderCode.provider == provider,
            VariantOptionProviderCode.vehicle_kind == vehicle_kind,
            VariantOptionProviderCode.code_group == code_group,
            VariantOptionProviderCode.provider_code == provider_code,
        )
        .where(
            ~select(VariantOptionEquipmentFeature.id)
            .where(
                VariantOptionEquipmentFeature.variant_option_id == VariantOptionProviderCode.variant_option_id,
                VariantOptionEquipmentFeature.feature_value_code == value_code,
            )
            .exists()
        )
        .order_by(VariantOptionProviderCode.variant_option_id)
    ).all()
    for tenant_id, variant_option_id in blocked:
        db.add(
            VariantOptionEquipmentFeature(
                tenant_id=tenant_id, variant_option_id=variant_option_id, feature_value_code=value_code
            )
        )
    db.flush()
    return len(blocked)


def apply_resolved_code_to_colours_and_tyres(
    db: Session, *, provider: str, vehicle_kind: str, code_group: str, provider_code: str, value_code: str,
) -> int:
    """Fills `value_code` into every cached colour or tyre row, in every
    tenant, whose raw code is exactly this one and whose value is still NULL —
    the rows a just-resolved `colour_area`, `axle_position` or `tyre_type` gap
    had left unclassified (KAN-75, Anto's B1: at once, as options since
    KAN-250). A row is matched to the gap's provider and vehicle kind through
    its variant's recorded raw `FzArt` (`VariantProviderCode`, the code the
    sync resolved it under). Any other code group fills nothing. Returns how
    many rows were filled.

    One conditional UPDATE per table, in the resolve's transaction: the NULL
    test runs under the row lock, so a row a concurrent sync has just filled
    is skipped. A sync visit of the same row resolves the code (and so bumps
    the gap the resolve holds) before it writes the value, so it either
    waits for the resolve and then reads the mapping, or committed before it
    and this UPDATE fills what it left NULL.
    """

    targets: dict[str, tuple[type[ColourCache | TyreSpecCache], InstrumentedAttribute[str | None], str]] = {
        _COLOUR_AREA: (ColourCache, ColourCache.colour_area_code, "colour_type"),
        _AXLE_POSITION: (TyreSpecCache, TyreSpecCache.axle_code, "axle"),
        _TYRE_TYPE: (TyreSpecCache, TyreSpecCache.season_code, "season"),
    }
    if code_group not in targets:
        return 0
    model, raw_column, value_attribute = targets[code_group]
    value_column = getattr(model, value_attribute)
    variants_of_kind = select(VariantProviderCode.model_variant_id).where(
        VariantProviderCode.provider == provider,
        VariantProviderCode.code_group == "vehicle_kind",
        VariantProviderCode.provider_code == vehicle_kind,
    )
    filled = db.scalars(
        update(model)
        .where(raw_column == provider_code, value_column.is_(None), model.model_variant_id.in_(variants_of_kind))
        .values({value_attribute: value_code})
        .returning(model.id)
        .execution_options(synchronize_session="fetch")
    ).all()
    db.flush()
    return len(filled)


def _is(column, value: str | None):
    """`column = value`, or `IS NULL` for a code the provider did not send."""

    return column.is_(None) if value is None else column == value


def _link_type_approvals(db: Session, *, variant: ModelVariant, type_approval_numbers: list[str]) -> None:
    """KAN-42 (C-D): records the variant's Typenscheine so FR-C-02 step 4's
    reverse lookup (`find_model_variants_by_type_approval`) can find a
    synced variant at all — before this, nothing outside a smoke-test seed
    wrote `vehicle_variant_type_approval`, so the Typenschein rung (and a
    plate that resolves through it) could never reach a synced car.

    Additive and idempotent: an existing `TypeApproval` row for the number
    is reused (the column is deliberately not unique, so the first row
    found is taken), an existing link is left alone, and a Typenschein the
    provider stops reporting is not unlinked here — a lookup never removes
    catalogue facts.
    """

    for number in dict.fromkeys(n.strip().upper() for n in type_approval_numbers if n and n.strip()):
        approval = db.scalar(
            select(TypeApproval)
            .where(TypeApproval.type_approval_number == number)
            .order_by(TypeApproval.created_at, TypeApproval.id)
            .limit(1)
        )
        if approval is None:
            approval = TypeApproval(type_approval_number=number)
            db.add(approval)
            db.flush()
        linked = db.get(VariantTypeApproval, (variant.id, approval.id))
        if linked is None:
            db.add(VariantTypeApproval(model_variant_id=variant.id, type_approval_id=approval.id))
    db.flush()


def _sync_option_equipment_features(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, vehicle_kind_code: str,
    variant_option: VariantOption, feature_codes: list[str],
) -> None:
    """`SuchCode` — a genuinely many-valued attribute of the option (unlike
    every other field synced in this module), so it is replaced wholesale
    on each sync rather than only ever grown: a feature the provider drops
    from an option must disappear locally too, not accrete forever. A
    provider code that fails to resolve is skipped (and left to
    `resolve_provider_code`'s own MappingGap, PR-8's admin queue) rather
    than blocking the rest of this option's sync.

    The raw code list itself is kept too (`VariantOptionProviderCode`,
    replaced wholesale the same way), resolved or not, so resolving a gap
    later adds the feature at once (`apply_resolved_code_to_options`,
    KAN-250).
    """

    _record_option_raw_codes(
        db, tenant_id=tenant_id, provider_code=provider_code, vehicle_kind_code=vehicle_kind_code,
        variant_option=variant_option, feature_codes=feature_codes,
    )
    resolved_codes = set()
    for raw_code in feature_codes:
        canonical = resolve_provider_code(
            db, provider=provider_code, vehicle_kind=vehicle_kind_code, code_group=_EQUIPMENT_FEATURE,
            provider_code=raw_code,
        )
        if canonical is not None:
            resolved_codes.add(canonical.value_code)

    existing = list(
        db.scalars(
            select(VariantOptionEquipmentFeature).where(
                VariantOptionEquipmentFeature.variant_option_id == variant_option.id
            )
        )
    )
    existing_codes = {row.feature_value_code for row in existing}
    for row in existing:
        if row.feature_value_code not in resolved_codes:
            db.delete(row)
    for code in resolved_codes - existing_codes:
        db.add(
            VariantOptionEquipmentFeature(
                tenant_id=tenant_id, variant_option_id=variant_option.id, feature_value_code=code
            )
        )


def _record_option_raw_codes(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, vehicle_kind_code: str,
    variant_option: VariantOption, feature_codes: list[str],
) -> None:
    """Keeps the option's `VariantOptionProviderCode` rows equal to the raw
    `SuchCode` list the provider sent this time: a new code is written, one
    the provider no longer sends removed, and a changed vehicle kind
    updated."""

    wanted = dict.fromkeys(code for code in feature_codes if code)
    rows = {
        row.provider_code: row
        for row in db.scalars(
            select(VariantOptionProviderCode).where(
                VariantOptionProviderCode.variant_option_id == variant_option.id,
                VariantOptionProviderCode.provider == provider_code,
                VariantOptionProviderCode.code_group == _EQUIPMENT_FEATURE,
            )
        )
    }
    for raw_code, row in rows.items():
        if raw_code not in wanted:
            db.delete(row)
        elif row.vehicle_kind != vehicle_kind_code:
            row.vehicle_kind = vehicle_kind_code
    for raw_code in wanted:
        if raw_code not in rows:
            db.add(
                VariantOptionProviderCode(
                    tenant_id=tenant_id, variant_option_id=variant_option.id, provider=provider_code,
                    vehicle_kind=vehicle_kind_code, code_group=_EQUIPMENT_FEATURE, provider_code=raw_code,
                )
            )
    db.flush()


def _sync_tenant_variant_content(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, model_variant: ModelVariant, fz_key: str,
    master: VariantMasterData, adapter, progress: _KeyProgress,
) -> None:
    """Options/colours/tyre-specs/images — all tenant-scoped, all upserted
    by their own natural key so a re-sync never duplicates a row.

    Nothing here consults an entitlement, and the four calls are not
    independent of each other: they share the lookup's one `vehicle_data`
    `call_capability` block and savepoint (`sync_catalogue_variant`), so a
    Datenname the provider refuses for this account (the protocol documents
    no "not entitled" signal — see `services/entitlement_probes.py` in
    `app.integration`) ends this variant's sync and is charged to the
    connection's breaker. Nothing this lookup wrote is kept: the gateway logs
    the failed request on a session of its own (KAN-90) and the savepoint
    rolls this variant's writes back, so the variant is never left
    half-synced. Degrading per capability (KAN-38 exit criterion 3) still
    needs these calls split by capability; until then `catalogue_entitlements`
    only ever degrades what is *read*, never what is fetched. `progress.field`
    is set before each call so a failure can say which one it was.

    C-0 PR 1: the real Datennamen need more than an `FzKey` —
    `Optionen` needs the model year, `OptionenFarben` the `Werkscode`,
    `PneuDimTS` a `TypSchNr`. All three come off `master`; a variant with
    no `werkscode` / no type-approval number simply skips that call.
    """

    progress.field = "options"
    for option in adapter.fetch_options(fz_key, model_year=model_variant.model_year_from):
        option_row = db.scalar(
            select(VariantOption).where(
                VariantOption.tenant_id == tenant_id, VariantOption.model_variant_id == model_variant.id,
                VariantOption.option_code == option.option_code,
            )
        )
        if option_row is None:
            option_row = VariantOption(
                tenant_id=tenant_id, model_variant_id=model_variant.id, option_code=option.option_code
            )
            db.add(option_row)
        option_row.description = option.description
        option_row.option_group = option.option_group
        option_row.price = option.price
        # KAN-43 (C-E) — these three columns were added by C-A but never
        # populated by this sync until now.
        option_row.is_included = option.is_included
        option_row.is_package = option.is_package
        option_row.model_year = model_variant.model_year_from
        db.flush()  # option_row.id must exist before the child rows below reference it
        _sync_option_equipment_features(
            db, tenant_id=tenant_id, provider_code=provider_code, vehicle_kind_code=master.vehicle_kind_code,
            variant_option=option_row, feature_codes=option.equipment_feature_codes,
        )

    # KAN-75: FarbArt, AchsenCode and PneuTyp arrive raw and resolve through
    # the code map like every other coded field — an unknown code is a
    # mapping gap and leaves the value NULL, never a guess (FR-C-10). The raw
    # code stays on the row for the resolve (`apply_resolved_code_to_colours_and_tyres`).
    def resolve(code_group: str, raw_code: str | None) -> str | None:
        return _resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group=code_group, provider_value=raw_code,
        )

    progress.field = "colours"
    for colour in (adapter.fetch_colours(werkscode=master.werkscode) if master.werkscode else []):
        # Resolved before the row is touched: the lookup may flush, and a new
        # row must not reach the database half-filled.
        colour_type = resolve(_COLOUR_AREA, colour.colour_area_code)
        colour_row = db.scalar(
            select(ColourCache).where(
                ColourCache.tenant_id == tenant_id, ColourCache.model_variant_id == model_variant.id,
                ColourCache.colour_code == colour.colour_code,
            )
        )
        if colour_row is None:
            colour_row = ColourCache(
                tenant_id=tenant_id, model_variant_id=model_variant.id, colour_code=colour.colour_code
            )
            db.add(colour_row)
        colour_row.description = colour.description
        colour_row.colour_area_code = colour.colour_area_code
        colour_row.colour_type = colour_type
        colour_row.price = colour.price

    _type_approval = master.type_approval_numbers[0] if master.type_approval_numbers else None
    progress.field = "tyre_specs"
    for tyre in (adapter.fetch_tyre_specs(type_approval_number=_type_approval) if _type_approval else []):
        axle = resolve(_AXLE_POSITION, tyre.axle_code)
        season = resolve(_TYRE_TYPE, tyre.season_code)
        tyre_row = db.scalar(
            select(TyreSpecCache).where(
                TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == model_variant.id,
                _is(TyreSpecCache.axle_code, tyre.axle_code), _is(TyreSpecCache.season_code, tyre.season_code),
            )
        )
        if tyre_row is None:
            tyre_row = TyreSpecCache(
                tenant_id=tenant_id, model_variant_id=model_variant.id, axle_code=tyre.axle_code,
                season_code=tyre.season_code,
            )
            db.add(tyre_row)
        tyre_row.axle = axle
        tyre_row.season = season
        tyre_row.size = tyre.size
        tyre_row.load_index = tyre.load_index
        tyre_row.speed_rating = tyre.speed_rating
        tyre_row.remark = tyre.remark

    progress.field = "images"
    for image in adapter.fetch_images(fz_key):
        image_row = db.scalar(
            select(ImageRef).where(
                ImageRef.tenant_id == tenant_id, ImageRef.model_variant_id == model_variant.id,
                ImageRef.image_key == image.image_key,
            )
        )
        if image_row is None:
            image_row = ImageRef(tenant_id=tenant_id, model_variant_id=model_variant.id, image_key=image.image_key)
            db.add(image_row)
        image_row.bild_typ = image.bild_typ
        image_row.bild_art = image.bild_art
        image_row.sequence = image.sequence
        image_row.image_url = image.image_url

    db.flush()


def sync_catalogue_variant(
    db: Session, *, tenant_id: uuid.UUID, fz_key: str, actor_id: uuid.UUID | None = None,
    purpose: str = "variant_lookup",
) -> SkippedFzKey | None:
    """One variant, fetched on its own: its master data, then its options,
    colours, tyres and images — a single lookup, the only way this module
    reaches the provider (ADR-023 as amended 2026-10-29; module docstring).
    Not wired to a screen yet: loading a variant's details when it is opened
    is KAN-211.

    The whole lookup is one `call_capability` block and one savepoint: the
    gateway logs each provider request (KAN-486) and charges or resets the
    breaker once for the block; a provider failure rolls this variant's
    writes back and comes back as the `SkippedFzKey` naming the call it was
    on, so a variant is never left half-synced. A synced variant is
    committed. A `ProviderGatewayError` raised before the block is entered is
    about the connection, not this key — the breaker open, the connection
    disabled or refused (KAN-489), an adapter that will not build — and is
    raised as it came.
    """

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        raise NoVehicleDataConnectionError(tenant_id)
    connection, provider_code = found
    progress = _KeyProgress()
    try:
        with call_capability(
            db, connection=connection, capability="vehicle_data", actor_id=actor_id, purpose=purpose,
        ) as adapter:
            progress.entered = True
            # Rolled back on any failure before the gateway sees it, so a
            # refused call leaves nothing half-written (KAN-90's rule).
            with db.begin_nested():
                master = adapter.fetch_vehicle_master_data(fz_key)
                variant = upsert_model_variant(db, provider_code=provider_code, master=master)
                _sync_tenant_variant_content(
                    db, tenant_id=tenant_id, provider_code=provider_code, model_variant=variant, fz_key=fz_key,
                    master=master, adapter=adapter, progress=progress,
                )
    except ProviderGatewayError as exc:
        if not progress.entered:
            raise
        return SkippedFzKey(fz_key=fz_key, field=progress.field, error=type(exc).__name__)
    db.commit()
    return None
