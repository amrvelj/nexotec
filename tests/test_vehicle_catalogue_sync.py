"""WP-6 PR-4: the per-tenant catalogue mirror sync — full seed, daily
delta with its exact 3-month `ChangedSince` hard limit, and the A-12
sync-age alarm as a pure function over persisted state.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, VariantOption, VariantOptionEquipmentFeature
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, ProviderSyncState, TyreSpecCache
from app.vehicle.models.provider import MappingGap, ProviderCodeMap
from app.vehicle.services import catalogue_sync


def _bare_variant(db_session, name: str = "Bare Variant") -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:8]}", display_name="Brand")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Group")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(model_group_id=group.id, name=name, model_year_from=2022)
    db_session.add(variant)
    db_session.flush()
    return variant


def _make_mock_provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock",
        category="vehicle_data",
        display_name="auto-i-dat (mock)",
        auth_type="none",
        required_secret_slots=[],
        capability_codes=["vehicle_data", "images", "packages", "valuation", "forecast"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id=None):
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


# --- seed --------------------------------------------------------------


def test_seed_tenant_catalogue_creates_global_variants_and_tenant_scoped_content(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    result = catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    assert result.variants_synced == 3
    assert db_session.query(ModelVariant).count() == 3
    assert db_session.query(VariantOption).filter_by(tenant_id=tenant_id).count() >= 1
    assert db_session.query(ColourCache).filter_by(tenant_id=tenant_id).count() == 3 * 3  # 3 colours per variant
    assert db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id).count() == 3 * 3  # front + rear + both
    assert db_session.query(ImageRef).filter_by(tenant_id=tenant_id).count() == 3

    state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert state is not None
    assert state.last_full_seed_at is not None
    assert state.last_system_watermark_date is not None


def test_seed_tenant_catalogue_raises_without_an_enabled_connection(db_session):
    with pytest.raises(catalogue_sync.NoVehicleDataConnectionError):
        catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=uuid.uuid4())


def test_seed_is_idempotent_reruns_do_not_duplicate_variants_or_tenant_content(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    assert db_session.query(ModelVariant).count() == 3  # never duplicated across two full seeds
    assert db_session.query(ColourCache).filter_by(tenant_id=tenant_id).count() == 3 * 3


def test_seed_persists_the_variant_base_price_the_adapter_already_returns(db_session):
    """C-A: `VariantMasterData.base_price` (LetzterNP) was being dropped —
    the adapter returns it, nothing persisted it. `base_price_year` /
    `price_is_net` stay NULL until C-0 wires FahrzeugePreise / NettoPreis."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    priced = [v for v in db_session.query(ModelVariant).all() if v.base_price is not None]
    assert len(priced) == 3
    assert {str(v.base_price) for v in priced} == {"28900.00", "42500.00", "54900.00"}
    assert all(v.base_price_year is None and v.price_is_net is None for v in priced)


def test_seed_writes_a_mapping_gap_on_an_unresolved_provider_code(db_session):
    """No ProviderCodeMap rows exist for `auto_i_dat_mock` in this test —
    every *_code field on every demo variant misses, and the existing
    WP-5 mapping-gap machinery (never rebuilt, just called) writes one row
    per distinct (provider, vehicle_kind, code_group, provider_code), with
    occurrences bumped on repeats rather than duplicated.
    """

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    gaps = db_session.query(MappingGap).filter_by(provider="auto_i_dat_mock").all()
    assert len(gaps) > 0
    assert all(gap.resolved is False for gap in gaps)
    # Every demo variant shares vehicle_kind_code="1" (see auto_i_dat_mock.py's
    # own fixture data) — the vehicle_kind gap for provider_code "1" is hit
    # by all three, so its occurrences count reflects that, never three
    # separate rows for the identical miss.
    vehicle_kind_gap = next(g for g in gaps if g.code_group == "vehicle_kind" and g.provider_code == "1")
    assert vehicle_kind_gap.occurrences == 3


# --- KAN-43 (C-E): options/colours/tyres field wiring --------------------


def test_seed_populates_option_included_package_and_model_year(db_session):
    """`is_included` / `is_package` / `model_year` were added to
    `VariantOption` by C-A but never populated by this sync until now."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    options = {
        o.option_code: o
        for o in db_session.query(VariantOption).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert options["WNTR"].is_package is True
    assert options["WNTR"].is_included is False
    assert options["AC"].is_included is True
    assert options["AC"].is_package is False
    assert all(o.model_year == golf.model_year_from for o in options.values())


def test_seed_resolves_equipment_features_via_provider_code_map(db_session):
    """`SuchCode` resolves through the same `resolve_provider_code`/
    `MappingGap` machinery every other coded field uses — a mapped code
    becomes a `VariantOptionEquipmentFeature` row, an unmapped one is left
    off the option and surfaces as a gap instead (FR-C-06/ADR-072's own
    "never silently dropped" posture, applied here to SuchCode)."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock", vehicle_kind="1", code_group="equipment_feature",
            provider_code="navigation", canonical_list_code="equipment_feature", canonical_value_code="navigation",
        )
    )
    db_session.commit()

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    nav = (
        db_session.query(VariantOption)
        .filter_by(tenant_id=tenant_id, model_variant_id=golf.id, option_code="NAV")
        .one()
    )
    feature_codes = {link.feature_value_code for link in nav.equipment_feature_links}
    # "navigation" resolves (mapped above); "APPLE_CARPLAY" does not (no
    # mapping row exists for it) and is left off rather than stored raw.
    assert feature_codes == {"navigation"}

    gap = (
        db_session.query(MappingGap)
        .filter_by(provider="auto_i_dat_mock", code_group="equipment_feature", provider_code="APPLE_CARPLAY")
        .one()
    )
    assert gap.resolved is False


def test_sync_option_equipment_features_replaces_the_set_on_each_call(db_session):
    """Unlike every other field in this sync (upsert-and-update-in-place,
    never delete), an option's equipment-feature set is genuinely
    many-valued: a feature the provider stops returning for an option must
    disappear locally too, or a corrected SuchCode mapping would only ever
    grow the set."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    option = VariantOption(
        tenant_id=tenant_id, model_variant_id=variant.id, option_code="NAV", description="Navigation"
    )
    db_session.add(option)
    db_session.flush()
    db_session.add(
        ProviderCodeMap(
            provider="auto_i_dat_mock", vehicle_kind="1", code_group="equipment_feature",
            provider_code="navigation", canonical_list_code="equipment_feature", canonical_value_code="navigation",
        )
    )
    db_session.commit()

    catalogue_sync._sync_option_equipment_features(
        db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock", vehicle_kind_code="1",
        variant_option=option, feature_codes=["navigation"],
    )
    db_session.commit()
    codes = {
        r.feature_value_code
        for r in db_session.query(VariantOptionEquipmentFeature).filter_by(variant_option_id=option.id)
    }
    assert codes == {"navigation"}

    catalogue_sync._sync_option_equipment_features(
        db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock", vehicle_kind_code="1",
        variant_option=option, feature_codes=[],
    )
    db_session.commit()
    codes = {
        r.feature_value_code
        for r in db_session.query(VariantOptionEquipmentFeature).filter_by(variant_option_id=option.id)
    }
    assert codes == set()


def test_seed_populates_colour_surcharge_price(db_session):
    """FR-C-07: "the surcharge is a price line in build mode." `Preis`
    was on the raw response but parsed by no one until now."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    colours = {
        c.colour_code: c
        for c in db_session.query(ColourCache).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert colours["BLK"].price == Decimal("0.00")
    assert colours["RED"].price == Decimal("1100.00")
    assert colours["GRY"].price is None  # interior colour, no surcharge in the fixture


def test_seed_populates_tyre_remark_and_season(db_session):
    """FR-C-08: BemDe ("nur mit Leichtmetallfelgen") and PneuTyp
    (summer/winter) must be shown with the dimension."""

    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id)

    golf = db_session.query(ModelVariant).filter_by(name="Golf GTI 2.0 TSI DSG").one()
    specs = {
        (t.axle, t.season): t
        for t in db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id, model_variant_id=golf.id).all()
    }
    assert specs[("front", "summer")].remark == "nur mit Leichtmetallfelgen"
    assert specs[("rear", "summer")].remark == "nur mit Leichtmetallfelgen"
    assert specs[("both", "winter")].remark is None


def test_tyre_spec_cache_allows_the_same_axle_with_two_different_seasons(db_session):
    """The bug this ticket fixes: the original unique constraint keyed only
    on (tenant, variant, axle), so a variant's summer and winter specs for
    the same axle silently overwrote each other on upsert."""

    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    db_session.add(
        TyreSpecCache(tenant_id=tenant_id, model_variant_id=variant.id, axle="front", season="summer", size="X")
    )
    db_session.flush()
    db_session.add(
        TyreSpecCache(tenant_id=tenant_id, model_variant_id=variant.id, axle="front", season="winter", size="Y")
    )
    db_session.flush()  # no IntegrityError — season is part of the key now

    rows = db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id, model_variant_id=variant.id).all()
    assert {(r.season, r.size) for r in rows} == {("summer", "X"), ("winter", "Y")}


def test_tyre_spec_cache_still_rejects_a_true_duplicate(db_session):
    tenant_id = uuid.uuid4()
    variant = _bare_variant(db_session)
    db_session.add(
        TyreSpecCache(tenant_id=tenant_id, model_variant_id=variant.id, axle="front", season="summer", size="X")
    )
    db_session.flush()
    db_session.add(
        TyreSpecCache(tenant_id=tenant_id, model_variant_id=variant.id, axle="front", season="summer", size="Z")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- daily delta ---------------------------------------------------------


def test_delta_with_no_prior_sync_state_falls_back_to_full_reseed(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    result = catalogue_sync.run_daily_delta_for_tenant(db_session, tenant_id=tenant_id)

    assert result.fell_back_to_full_reseed is True
    assert result.variants_synced == 3


def test_delta_within_the_three_month_window_runs_a_normal_delta(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    seed_day = dt.date(2026, 6, 1)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id, today=seed_day)

    next_day = seed_day + dt.timedelta(days=1)
    result = catalogue_sync.run_daily_delta_for_tenant(db_session, tenant_id=tenant_id, today=next_day)

    assert result.fell_back_to_full_reseed is False
    state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert state.last_delta_cursor == next_day


def test_delta_past_the_three_month_hard_limit_refuses_and_falls_back_to_full_reseed(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)

    stale_day = dt.date(2026, 1, 1)
    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id, today=stale_day)

    today = stale_day + dt.timedelta(days=100)  # past the 90-day hard limit
    result = catalogue_sync.run_daily_delta_for_tenant(db_session, tenant_id=tenant_id, today=today)

    assert result.fell_back_to_full_reseed is True
    state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    assert state.last_delta_cursor == today  # the fallback reseed still advances the cursor to today


def test_delta_raises_without_an_enabled_connection(db_session):
    with pytest.raises(catalogue_sync.NoVehicleDataConnectionError):
        catalogue_sync.run_daily_delta_for_tenant(db_session, tenant_id=uuid.uuid4())


# --- sync-age alarm (A-12) ------------------------------------------------


def test_compute_sync_age_alarm_does_not_fire_at_exactly_seven_days():
    today = dt.date(2026, 8, 31)
    state = ProviderSyncState(
        tenant_id=uuid.uuid4(), provider_code="auto_i_dat_mock", last_system_watermark_date=today - dt.timedelta(days=7)
    )
    assert catalogue_sync.compute_sync_age_alarm(state, today=today) is False


def test_compute_sync_age_alarm_fires_at_eight_days_even_though_nothing_else_failed():
    """A-12's own point: a delta job that "succeeds" while the provider's
    own System date hasn't moved in over a week is still an alarm — this
    function never looks at whether any job reported success, only at the
    persisted watermark age.
    """

    today = dt.date(2026, 8, 31)
    state = ProviderSyncState(
        tenant_id=uuid.uuid4(), provider_code="auto_i_dat_mock", last_system_watermark_date=today - dt.timedelta(days=8)
    )
    assert catalogue_sync.compute_sync_age_alarm(state, today=today) is True


def test_compute_sync_age_alarm_never_fires_for_a_tenant_that_has_not_synced_yet():
    assert catalogue_sync.compute_sync_age_alarm(None, today=dt.date(2026, 8, 31)) is False


def test_check_sync_age_alarm_for_tenant_returns_false_without_a_connection(db_session):
    assert catalogue_sync.check_sync_age_alarm_for_tenant(db_session, tenant_id=uuid.uuid4()) is False


def test_check_sync_age_alarm_for_tenant_reads_persisted_state(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=tenant_id)
    today = dt.date(2026, 8, 31)

    catalogue_sync.seed_tenant_catalogue(db_session, tenant_id=tenant_id, today=today - dt.timedelta(days=8))
    # Force the persisted watermark stale without another live call, the
    # same way a real 8-day-old provider System date would look on read.
    state = catalogue_sync.get_sync_state(db_session, tenant_id=tenant_id, provider_code="auto_i_dat_mock")
    state.last_system_watermark_date = today - dt.timedelta(days=8)
    db_session.commit()

    assert catalogue_sync.check_sync_age_alarm_for_tenant(db_session, tenant_id=tenant_id, today=today) is True
