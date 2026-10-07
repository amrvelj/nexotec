"""Per-tenant catalogue mirror sync (WP-6 PR-4) — the real work the
provider-gateway mock/real adapters exist to feed. `app.integration.public
.call_capability` is the only way this module ever reaches a provider;
this file resolves the connection itself via `get_enabled_connection`
(never handed one by a caller), so `app.integration` never needs to know
anything about vehicle's own tables — the same seam
`get_enabled_connection`'s own docstring describes.

Full seed vs. daily delta (ADR-023): a full per-tenant seed is flat-rate
billed (no marginal cost per call under the licence), so onboarding always
runs a full seed rather than a TTL cache that would just re-fetch the same
content repeatedly for no reason. The daily delta calls `FzKeyChanged`
with a `since` cursor that must never predate `today - 3 months`
(auto-i-dat's own hard limit on `ChangedSince`) — past that window this
module refuses the delta and falls back to a full reseed, loudly (the
caller sees `fell_back_to_full_reseed=True` on the result, never a silent
downgrade).

There is no dedicated "list every FzKey" capability in `ProviderAdapter` —
a full seed is modeled as a delta "since the epoch" (`list_changed_keys`
called with a date far in the past). Both adapters happen to already
return their complete key set for any `since` (the mock always does; the
real one is a provisional simplification, flagged here pending
confirmation of whether auto-i-dat exposes a genuinely separate bulk
export operation) — if that assumption is ever wrong for the real
provider, this is the one place to add a dedicated "full export" adapter
method.

The sync-age alarm (A-12) is a pure function over the persisted
`ProviderSyncState.last_system_watermark_date` — refreshed by every
delta/seed run regardless of whether that run found any changed keys —
so a delta job that "succeeds" while doing nothing still keeps the
watermark current, and the alarm still fires if the *provider's own*
System date stops moving forward, independent of the delta job's own
reported outcome.

Per-FzKey isolation (KAN-78): each FzKey is synced in its own
`call_capability` block, so a provider failure on one key (a Datenname the
account is refused, a transport error) is still logged as an ERROR call and
still charged to the connection's breaker by the gateway — and then caught
here, recorded on `SyncResult.skipped` and `ProviderSyncState.pending_fz_keys`,
and the run moves on to the next key. Only a `ProviderGatewayError` raised
*by a call* is isolated this way; anything else is a bug and still aborts
the run. A failure about the connection rather than the key — the breaker
open, the connection disabled, or the adapter failing to build (a WSDL that
will not load) — stops the loop: the keys not yet synced are recorded as
pending without a call, the watermark is not fetched, and the run ends
cleanly. A run therefore logs one call per FzKey plus one for
`FzKeyChanged` and one for `System`, not one per run — and, because the
gateway resolves an adapter per `call_capability`, builds the adapter that
many times too.
"""

import datetime as dt
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.public import (
    CircuitOpenError,
    IntegrationConnection,
    ProviderGatewayError,
    VariantMasterData,
    call_capability,
    get_enabled_connection,
)
from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    VariantOption,
    VariantOptionEquipmentFeature,
)
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, ProviderSyncState, TyreSpecCache
from app.vehicle.models.provider import ProviderEntityRef
from app.vehicle.services.provider import resolve_provider_code

logger = logging.getLogger("app.vehicle.catalogue_sync")

# Mock and real are separate provider_codes / separate connections, never
# a runtime flag (rule 7's own posture) — a tenant's own vehicle-data
# connection is whichever of these two it actually has enabled; trying
# both here is this module's own resolution step, not a flag on one
# connection.
VEHICLE_DATA_PROVIDER_CODES = ("auto_i_dat", "auto_i_dat_mock")

# auto-i-dat's own hard limit on FzKeyChanged's ChangedSince (PRD-Vehicles,
# ADR-023) — miss it and only a full resync recovers.
_DELTA_LOOKBACK_HARD_LIMIT_DAYS = 90
_SYNC_AGE_ALARM_THRESHOLD_DAYS = 7  # A-12
_EPOCH_FOR_FULL_SEED = dt.date(1970, 1, 1)

_ENTITY_TYPE_MODEL_VARIANT = "model_variant"


class NoVehicleDataConnectionError(Exception):
    """Raised only by callers that need to fail loudly on "no contract" —
    the daily job itself (app/integration/daily_jobs.py) never raises
    this; it simply never calls sync for a tenant with no enabled
    connection in the first place (PR-5's "fully usable module without a
    provider contract" applies to reads, not to a job that has nothing to
    do for that tenant).
    """

    def __init__(self, tenant_id: uuid.UUID) -> None:
        super().__init__(f"Tenant {tenant_id} has no enabled vehicle-data connection.")


# The value `SkippedFzKey.field` takes for a key the run never sent to the
# provider because the connection itself failed (see `_ConnectionFailed`).
NOT_ATTEMPTED: Final = "not_attempted"

SkippedField = Literal["master_data", "options", "colours", "tyre_specs", "images", "not_attempted"]


@dataclass(frozen=True)
class SkippedFzKey:
    """One FzKey a run did not sync in full. `field` is the call it failed
    on (`master_data`, `options`, `colours`, `tyre_specs`, `images`) or
    `NOT_ATTEMPTED`; `error` is the exception's class name, never its
    message.
    """

    fz_key: str
    field: SkippedField
    error: str


@dataclass(frozen=True)
class SyncResult:
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    provider_code: str
    variants_synced: int
    fell_back_to_full_reseed: bool = False
    skipped: tuple[SkippedFzKey, ...] = ()


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


def get_sync_state(db: Session, *, tenant_id: uuid.UUID, provider_code: str) -> ProviderSyncState | None:
    return db.scalar(
        select(ProviderSyncState).where(
            ProviderSyncState.tenant_id == tenant_id, ProviderSyncState.provider_code == provider_code
        )
    )


def list_all_sync_states(db: Session) -> list[ProviderSyncState]:
    """PR-7's fleet-wide health board — every tenant, every provider,
    platform-wide. The endpoint calling this (app/vehicle/api/
    catalogue_sync.py) is platform_admin-gated; this function itself does
    no tenant filtering at all, so it must never be reachable from a
    dealer-facing route.
    """

    return list(db.scalars(select(ProviderSyncState).order_by(ProviderSyncState.tenant_id)).all())


def _get_or_create_sync_state(db: Session, *, tenant_id: uuid.UUID, provider_code: str) -> ProviderSyncState:
    state = get_sync_state(db, tenant_id=tenant_id, provider_code=provider_code)
    if state is None:
        state = ProviderSyncState(tenant_id=tenant_id, provider_code=provider_code)
        db.add(state)
        db.flush()
    return state


def compute_sync_age_alarm(sync_state: ProviderSyncState | None, *, today: dt.date) -> bool:
    """A-12: fires strictly after 7 days, never at exactly 7 — and never
    at all for a tenant that has no sync state yet (no contract, or never
    synced), since "never started" is a different, PR-5-shaped concern
    from "started and then went stale".
    """

    if sync_state is None or sync_state.last_system_watermark_date is None:
        return False
    return (today - sync_state.last_system_watermark_date).days > _SYNC_AGE_ALARM_THRESHOLD_DAYS


def check_sync_age_alarm_for_tenant(db: Session, *, tenant_id: uuid.UUID, today: dt.date | None = None) -> bool:
    """Reads persisted state only — never makes a live provider call of
    its own. Correctness relies on `run_daily_delta_for_tenant` (or
    `seed_tenant_catalogue`) having refreshed `last_system_watermark_date`
    today, which the daily job composition root
    (app/integration/daily_jobs.py) always does before calling this.
    """

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        return False
    _connection, provider_code = found
    state = get_sync_state(db, tenant_id=tenant_id, provider_code=provider_code)
    return compute_sync_age_alarm(state, today=today or utcnow().date())


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
    if provider_value is None:
        return None
    canonical = resolve_provider_code(
        db, provider=provider_code, vehicle_kind=vehicle_kind_qualifier, code_group=code_group,
        provider_code=provider_value,
    )
    return canonical.value_code if canonical is not None else None


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
    provider_key=fz_key)` — the natural idempotency key for a re-run seed
    or a delta re-fetch of an already-known variant, so a variant that
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
    """

    existing = find_variant_by_fz_key(db, provider_code=provider_code, fz_key=master.fz_key)
    if existing is not None:
        return existing

    brand = _upsert_brand(db, display_name=master.brand_display_name)
    group = _upsert_model_group(db, brand=brand, name=master.model_group_name)

    variant = ModelVariant(
        model_group_id=group.id,
        name=master.variant_name,
        model_year_from=master.model_year_from,
        model_year_to=master.model_year_to,
        # C-A: the adapter already returns `base_price` (LetzterNP) and it
        # was being dropped on the floor. `base_price_year` / `price_is_net`
        # stay NULL until C-0 wires FahrzeugePreise / NettoPreis; the
        # per-model-year price history (`vehicle_variant_price`) is C-0/C-D.
        base_price=master.base_price,
        # C-0 PR 1: `Fahrzeuge.Werkscode` (p10) — persisted here because
        # `OptionenFarben` searches on it, not on `FzKey`.
        werkscode=master.werkscode,
        vehicle_kind=_resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group="vehicle_kind", provider_value=master.vehicle_kind_code,
        ),
        fuel_type=_resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group="fuel_type", provider_value=master.fuel_type_code,
        ),
        body_style=_resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group="body_style", provider_value=master.body_style_code,
        ),
        drivetrain=_resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group="drivetrain", provider_value=master.drivetrain_code,
        ),
        transmission=_resolve_code(
            db, provider_code=provider_code, vehicle_kind_qualifier=master.vehicle_kind_code,
            code_group="transmission", provider_value=master.transmission_code,
        ),
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
    return variant


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
    """

    resolved_codes = set()
    for raw_code in feature_codes:
        canonical = resolve_provider_code(
            db, provider=provider_code, vehicle_kind=vehicle_kind_code, code_group="equipment_feature",
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


def _sync_tenant_variant_content(
    db: Session, *, tenant_id: uuid.UUID, provider_code: str, model_variant: ModelVariant, fz_key: str,
    master: VariantMasterData, adapter, progress: _KeyProgress,
) -> None:
    """Options/colours/tyre-specs/images — all tenant-scoped, all upserted
    by their own natural key so a re-sync never duplicates a row.

    Nothing here consults an entitlement, and the four calls are not
    independent of each other: they share this FzKey's one `vehicle_data`
    `call_capability` block (`_sync_one_key`), so a Datenname the provider
    refuses for this account (the protocol documents no "not entitled"
    signal — see `services/entitlement_probes.py` in `app.integration`)
    ends this FzKey's sync and is charged to the connection's breaker. That
    error path commits this session to write the call-log row, so the
    variant is left partially synced (e.g. its options, colours and tyre
    specs persisted, its images not) — which is why the key goes onto
    `ProviderSyncState.pending_fz_keys` and is retried next run. Other
    FzKeys in the run are unaffected (KAN-78). Degrading per capability
    (KAN-38 exit criterion 3) still needs these calls split by capability;
    until then `catalogue_entitlements` only ever degrades what is *read*,
    never what is fetched. `progress.field` is set before each call so a
    failure can say which one it was.

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

    progress.field = "colours"
    for colour in (adapter.fetch_colours(werkscode=master.werkscode) if master.werkscode else []):
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
        colour_row.colour_type = colour.colour_type
        colour_row.price = colour.price

    _type_approval = master.type_approval_numbers[0] if master.type_approval_numbers else None
    progress.field = "tyre_specs"
    for tyre in (adapter.fetch_tyre_specs(type_approval_number=_type_approval) if _type_approval else []):
        tyre_row = db.scalar(
            select(TyreSpecCache).where(
                TyreSpecCache.tenant_id == tenant_id, TyreSpecCache.model_variant_id == model_variant.id,
                TyreSpecCache.axle == tyre.axle, TyreSpecCache.season == tyre.season,
            )
        )
        if tyre_row is None:
            tyre_row = TyreSpecCache(
                tenant_id=tenant_id, model_variant_id=model_variant.id, axle=tyre.axle, season=tyre.season
            )
            db.add(tyre_row)
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


@dataclass
class _RunOutcome:
    variants_synced: int = 0
    skipped: list[SkippedFzKey] = field(default_factory=list)
    # Set when the connection itself failed mid-run; the run stops calling
    # the provider and does not fetch the watermark.
    connection_failed: bool = False


class _ConnectionFailed(Exception):
    """A per-key `call_capability` failed before its block was entered —
    the breaker is open, the connection is disabled, or the adapter could
    not be built. None of that is about the key, so the loop stops."""

    def __init__(self, cause: ProviderGatewayError) -> None:
        super().__init__(type(cause).__name__)
        self.error = type(cause).__name__


def _sync_one_key(
    db: Session, *, connection: IntegrationConnection, tenant_id: uuid.UUID, provider_code: str, fz_key: str,
    actor_id: uuid.UUID | None, purpose: str,
) -> SkippedFzKey | None:
    """One FzKey in its own `call_capability` block: the gateway logs it
    (SUCCESS or ERROR), charges or resets the breaker, commits, and
    re-raises a failure — which is caught here and returned as the skip.

    A `ProviderGatewayError` raised before the block is entered comes from
    the gateway resolving the adapter (`CircuitOpenError`,
    `ConnectionDisabledError`, a WSDL that will not load). The gateway
    neither logs nor charges those, and they are about the connection, not
    this key — so they are raised as `_ConnectionFailed` for the caller to
    stop on.
    """

    progress = _KeyProgress()
    try:
        with call_capability(
            db, connection=connection, capability="vehicle_data", actor_id=actor_id, purpose=purpose,
        ) as adapter:
            progress.entered = True
            master = adapter.fetch_vehicle_master_data(fz_key)
            variant = upsert_model_variant(db, provider_code=provider_code, master=master)
            _sync_tenant_variant_content(
                db, tenant_id=tenant_id, provider_code=provider_code, model_variant=variant, fz_key=fz_key,
                master=master, adapter=adapter, progress=progress,
            )
    except ProviderGatewayError as exc:
        if not progress.entered:
            raise _ConnectionFailed(exc) from exc
        return SkippedFzKey(fz_key=fz_key, field=progress.field, error=type(exc).__name__)
    return None


def _sync_keys(
    db: Session, *, connection: IntegrationConnection, tenant_id: uuid.UUID, provider_code: str,
    fz_keys: list[str], actor_id: uuid.UUID | None, purpose: str,
) -> _RunOutcome:
    """Every key is attempted unless the connection itself fails; a
    provider failure on one key is recorded and the loop moves on. Once the
    connection fails (`_ConnectionFailed`), that key and every later one
    are recorded as `NOT_ATTEMPTED` without a call. Anything that is not a
    `ProviderGatewayError` — a bug, a database error — propagates and
    aborts the run, as before.
    """

    outcome = _RunOutcome()
    for index, fz_key in enumerate(fz_keys):
        try:
            skipped = _sync_one_key(
                db, connection=connection, tenant_id=tenant_id, provider_code=provider_code, fz_key=fz_key,
                actor_id=actor_id, purpose=purpose,
            )
        except _ConnectionFailed as failure:
            outcome.connection_failed = True
            outcome.skipped.extend(
                SkippedFzKey(fz_key=key, field=NOT_ATTEMPTED, error=failure.error) for key in fz_keys[index:]
            )
            break
        if skipped is None:
            outcome.variants_synced += 1
        else:
            outcome.skipped.append(skipped)
    return outcome


def _keys_to_sync(changed: list[str], state: ProviderSyncState | None) -> list[str]:
    """What the provider reports changed, then every key a previous run
    left pending that is not already in that list — order kept, no key
    twice.
    """

    keys = list(dict.fromkeys(changed))
    seen = set(keys)
    for entry in (state.pending_fz_keys if state is not None else []):
        if entry["fz_key"] not in seen:
            keys.append(entry["fz_key"])
            seen.add(entry["fz_key"])
    return keys


def _run(
    db: Session, *, connection: IntegrationConnection, tenant_id: uuid.UUID, provider_code: str,
    since: dt.date, actor_id: uuid.UUID | None, purpose: str, today: dt.date, full_seed: bool,
) -> SyncResult:
    with call_capability(
        db, connection=connection, capability="vehicle_data", actor_id=actor_id, purpose=purpose,
    ) as adapter:
        changed = adapter.list_changed_keys(since=since)

    state = get_sync_state(db, tenant_id=tenant_id, provider_code=provider_code)
    fz_keys = _keys_to_sync(changed, state)
    outcome = _sync_keys(
        db, connection=connection, tenant_id=tenant_id, provider_code=provider_code, fz_keys=fz_keys,
        actor_id=actor_id, purpose=purpose,
    )

    # Progress is written before the watermark call, so a run that skipped
    # keys — or whose watermark call fails — still advances the cursor and
    # keeps its pending list: nothing that failed is lost by moving on.
    run_at = utcnow()
    state = _get_or_create_sync_state(db, tenant_id=tenant_id, provider_code=provider_code)
    if full_seed:
        state.last_full_seed_at = run_at
    state.last_delta_cursor = today
    state.pending_fz_keys = [
        {"fz_key": s.fz_key, "field": s.field, "error": s.error, "failed_at": run_at.isoformat()}
        for s in outcome.skipped
    ]
    db.commit()

    # Not fetched once the connection has failed: the gateway would refuse
    # or fail it again, and leaving the watermark untouched lets the A-12
    # alarm notice a mirror that stops moving. The breaker can also open on
    # the run's last key, which the loop never sees — that refusal ends the
    # run the same way rather than raising out of it.
    if not outcome.connection_failed:
        try:
            with call_capability(
                db, connection=connection, capability="vehicle_data", actor_id=actor_id, purpose=purpose,
            ) as adapter:
                watermark = adapter.get_system_watermark()
        except CircuitOpenError:
            outcome.connection_failed = True
        else:
            state.last_system_watermark_date = watermark.update_date
            state.last_system_checked_at = utcnow()
            db.commit()

    if outcome.skipped or outcome.connection_failed:
        logger.warning(
            "catalogue sync skipped %d of %d FzKeys", len(outcome.skipped), len(fz_keys),
            extra={
                "tenantId": str(tenant_id), "providerCode": provider_code,
                "connectionFailed": outcome.connection_failed,
            },
        )

    return SyncResult(
        tenant_id=tenant_id, connection_id=connection.id, provider_code=provider_code,
        variants_synced=outcome.variants_synced, skipped=tuple(outcome.skipped),
    )


def seed_tenant_catalogue(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None = None, today: dt.date | None = None,
) -> SyncResult:
    """Full seed — every FzKey the connection's own account can see, flat-
    rate billed (ADR-023), no TTL cache. Run once at onboarding, and
    again automatically whenever a daily delta's own cursor has aged past
    the 3-month `ChangedSince` hard limit.
    """

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        raise NoVehicleDataConnectionError(tenant_id)
    connection, provider_code = found
    return _run(
        db, connection=connection, tenant_id=tenant_id, provider_code=provider_code, since=_EPOCH_FOR_FULL_SEED,
        actor_id=actor_id, purpose="seed", today=today or utcnow().date(), full_seed=True,
    )


def run_daily_delta_for_tenant(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None = None, today: dt.date | None = None,
) -> SyncResult:
    """The daily job's own per-tenant step. Refuses a delta whose cursor
    predates the 3-month `ChangedSince` hard limit and falls back to a
    full reseed instead of ever sending a request auto-i-dat would
    reject — `fell_back_to_full_reseed=True` on the result makes this
    visible to the caller (and, via PR-7's sync-status endpoint, to a
    platform operator) rather than silent. Keys a previous run left
    pending are retried alongside what `FzKeyChanged` reports (KAN-78).
    """

    found = find_enabled_vehicle_data_connection(db, tenant_id=tenant_id)
    if found is None:
        raise NoVehicleDataConnectionError(tenant_id)
    connection, provider_code = found
    today = today or utcnow().date()

    state = get_sync_state(db, tenant_id=tenant_id, provider_code=provider_code)
    since = state.last_delta_cursor if state is not None else None
    hard_limit = today - dt.timedelta(days=_DELTA_LOOKBACK_HARD_LIMIT_DAYS)
    if since is None or since < hard_limit:
        result = seed_tenant_catalogue(db, tenant_id=tenant_id, actor_id=actor_id, today=today)
        return SyncResult(
            tenant_id=result.tenant_id, connection_id=result.connection_id, provider_code=result.provider_code,
            variants_synced=result.variants_synced, fell_back_to_full_reseed=True, skipped=result.skipped,
        )

    return _run(
        db, connection=connection, tenant_id=tenant_id, provider_code=provider_code, since=since,
        actor_id=actor_id, purpose="delta", today=today, full_seed=False,
    )
