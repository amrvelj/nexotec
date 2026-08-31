"""WP-6 PR-4: the per-tenant catalogue mirror sync — full seed, daily
delta with its exact 3-month `ChangedSince` hard limit, and the A-12
sync-age alarm as a pure function over persisted state.
"""

import datetime as dt
import uuid

import pytest

from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.vehicle.models.catalogue import ModelVariant, VariantOption
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, ProviderSyncState, TyreSpecCache
from app.vehicle.models.provider import MappingGap
from app.vehicle.services import catalogue_sync


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
    assert db_session.query(ColourCache).filter_by(tenant_id=tenant_id).count() == 3 * 2  # 2 colours per variant
    assert db_session.query(TyreSpecCache).filter_by(tenant_id=tenant_id).count() == 3 * 2  # front + rear
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
    assert db_session.query(ColourCache).filter_by(tenant_id=tenant_id).count() == 3 * 2


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
