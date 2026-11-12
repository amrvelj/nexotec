"""KAN-547 exit criterion 2 — the identification waterfall on the identAPI,
through the real registered adapter over the fake authorization server and
identAPI (`tests/identapi_support.py`, bodies from `tests/identapi_fixtures.py`).

Per rung: the happy path, an empty answer (404 → no match, falls through),
a 403 → «not entitled» note, a timeout → «unavailable» note; a Wechselschild
yields the picker; the second lookup of the same plate within 30 days makes
no call. Every variant a rung returns is stored with its spec block (ADR-075,
PR 1), so a repeated lookup finds it without the provider.
"""

from __future__ import annotations

import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.integration.models.call_log import IntegrationCallLog
from app.integration.services import connections as connection_service
from app.integration.services import resilience
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.configuration import ConfigurationMatchMethod
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services import identification as svc
from app.vehicle.services.identification import IdentificationNote, IdentificationOutcome, IdentifierKind
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi
from tests.test_provider_code_map_seed import _seed_migration

PLATE = "ZH911001"
STAMMNUMMER = "900911001"
PLATE_PATH = f"/vehiclebuild/licenseplate/{PLATE}"
VRN_PATH = f"/vehiclebuild/vrn/{STAMMNUMMER}"
TYPENSCHEIN_PATH = f"/vehiclecatalog/typeapproval/{fix.DEMO_TYPE_APPROVAL}"
WERKSCODE_PATH = f"/vehiclecatalog/factorycode/{fix.DEMO_WERKSCODE}"
VIN = "WVWZZZTESTV000911"  # invented; the fixture car answers it
VIN_PATH = f"/vehiclebuild/vin/{VIN}"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _connected(db_session, monkeypatch, fake: FakeAutoi):
    s = fx.connected(db_session, monkeypatch, fake=fake)
    _seed_migration()._seed(db_session.connection())
    db_session.commit()
    return s


def _calls(db_session, operation: str) -> int:
    return db_session.scalar(
        select(func.count()).select_from(IntegrationCallLog).where(IntegrationCallLog.capability == operation)
    )


def _identify(db_session, s, query: str):
    return svc.identify(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, query=query)


# --- Kontrollschild --------------------------------------------------------------------


def test_a_plate_is_one_vehiclebuild_call_and_the_registered_car_is_the_candidate(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle()])}))

    result = _identify(db_session, s, "ZH 911 001")

    assert result.kind is IdentifierKind.KONTROLLSCHILD and result.outcome is IdentificationOutcome.VARIANTS
    (candidate,) = result.variants
    assert (candidate.brand_display_name, candidate.variant_name, candidate.trim_name) == ("Testmarke", "2.0 TDI Avant quattro", "Sport")
    assert (candidate.ps, candidate.base_price_year) == (204, 2023)
    assert result.observed.stammnummer == STAMMNUMMER and result.observed.type_approval_number == fix.DEMO_TYPE_APPROVAL
    assert result.observed.first_registration_date == dt.date(2022, 5, 17)
    assert result.best_match_available is False and result.notes == []
    assert _calls(db_session, "vehiclebuild") == 1 and _calls(db_session, "vehiclecatalog") == 0
    # the variant is stored with its block (PR 1's path), the plate answer cached
    variant = db_session.get(ModelVariant, candidate.catalogue_variant_id)
    assert variant is not None and variant.doors == 5 and variant.fuel_type == "diesel"
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 1


def test_the_second_lookup_of_the_same_plate_within_30_days_makes_no_call(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle()])}))
    first = _identify(db_session, s, PLATE)
    calls_after_first = len(s.fake.api_requests())

    second = _identify(db_session, s, "zh 911001")

    assert len(s.fake.api_requests()) == calls_after_first
    assert second.notes == [IdentificationNote.PLATE_CACHE_HIT]
    assert second.outcome is IdentificationOutcome.VARIANTS and len(first.variants) == 1
    assert [v.catalogue_variant_id for v in second.variants] == [v.catalogue_variant_id for v in first.variants]
    assert second.observed.stammnummer == STAMMNUMMER and second.observed.first_registration_date == dt.date(2022, 5, 17)


def _two_variants_under_the_typenschein(db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={
            PLATE_PATH: fix.body([fix.built_vehicle()]),
            TYPENSCHEIN_PATH: fix.body([fix.catalog_variant(), fix.catalog_variant(fzkey="01911002", version="2.0 TDI Avant", edition=None)]),
        }),
    )
    _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)  # stores both variants under the Typenschein
    return s


def test_a_cache_hit_gives_the_variant_the_provider_named_not_a_picker(db_session, monkeypatch):
    """KAN-592 (option A, ADR-126): the cache row keeps the variant the live
    answer named, so the repeat lookup is the same single candidate — even
    where two stored variants share the Typenschein — with no call at all."""

    s = _two_variants_under_the_typenschein(db_session, monkeypatch)
    first = _identify(db_session, s, PLATE)
    assert [v.variant_name for v in first.variants] == ["2.0 TDI Avant quattro"] and first.best_match_available is False
    named = first.variants[0].catalogue_variant_id
    (row,) = db_session.scalars(select(PlateLookupCacheEntry)).all()
    assert row.model_variant_id == named
    typenschein_calls = len(s.fake.api_requests_to(TYPENSCHEIN_PATH))

    second = _identify(db_session, s, PLATE)

    assert IdentificationNote.PLATE_CACHE_HIT in second.notes
    assert second.outcome is IdentificationOutcome.VARIANTS
    assert [v.catalogue_variant_id for v in second.variants] == [named]
    assert second.best_match_available is False
    assert second.observed.stammnummer == STAMMNUMMER and second.observed.type_approval_number == fix.DEMO_TYPE_APPROVAL
    assert len(s.fake.api_requests_to(PLATE_PATH)) == 1
    assert len(s.fake.api_requests_to(TYPENSCHEIN_PATH)) == typenschein_calls, "no catalogue call either"


def test_a_cache_row_without_a_variant_is_still_the_picker_not_a_guess(db_session, monkeypatch):
    """A row written before KAN-592 (or for an answer that named no variant)
    continues at the Typenschein: both stored variants, best match offered."""

    s = _two_variants_under_the_typenschein(db_session, monkeypatch)
    _identify(db_session, s, PLATE)
    (row,) = db_session.scalars(select(PlateLookupCacheEntry)).all()
    row.model_variant_id = None
    db_session.commit()

    second = _identify(db_session, s, PLATE)

    assert IdentificationNote.PLATE_CACHE_HIT in second.notes
    assert sorted(v.variant_name for v in second.variants) == ["2.0 TDI Avant", "2.0 TDI Avant quattro"]
    assert second.best_match_available is True
    assert len(s.fake.api_requests_to(PLATE_PATH)) == 1


def test_a_cache_row_naming_a_variant_no_longer_stored_continues_at_the_typenschein(db_session, monkeypatch):
    s = _two_variants_under_the_typenschein(db_session, monkeypatch)
    _identify(db_session, s, PLATE)
    (row,) = db_session.scalars(select(PlateLookupCacheEntry)).all()
    row.model_variant_id = uuid.uuid4()
    db_session.commit()

    second = _identify(db_session, s, PLATE)

    assert sorted(v.variant_name for v in second.variants) == ["2.0 TDI Avant", "2.0 TDI Avant quattro"]
    assert len(s.fake.api_requests_to(PLATE_PATH)) == 1


def test_a_wechselschild_is_the_picker_and_nothing_is_chosen(db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle(), fix.built_vehicle(vrn="900.911.002", fzkey="01911002", typeApprovalNr="1ZZ912", version="2.0 TDI Avant")])}),
    )

    result = _identify(db_session, s, PLATE)

    assert result.outcome is IdentificationOutcome.PLATE_RECORDS and result.variants == []
    assert [r.stammnummer for r in result.plate_records] == [STAMMNUMMER, "900911002"]
    assert result.plate_records_interchangeable is True and result.plate_records_conflict is False
    assert _calls(db_session, "vehiclebuild") == 1
    # both variants were stored by the one answer
    assert db_session.scalar(select(func.count()).select_from(ModelVariant)) == 2
    # KAN-592: each record carries the variant the provider named as its one
    # candidate, live and cached alike
    named = [r.variant.catalogue_variant_id for r in result.plate_records if r.variant is not None]
    assert len(named) == 2 and len(set(named)) == 2
    assert set(named) == set(db_session.scalars(select(ModelVariant.id)).all())
    assert [r.variant.variant_name for r in result.plate_records] == ["2.0 TDI Avant quattro", "2.0 TDI Avant"]
    cached = _identify(db_session, s, PLATE)
    assert IdentificationNote.PLATE_CACHE_HIT in cached.notes
    assert [r.variant.catalogue_variant_id for r in cached.plate_records] == named
    # a cached record whose variant the catalogue no longer holds offers none
    db_session.query(PlateLookupCacheEntry).filter(PlateLookupCacheEntry.model_variant_id == named[1]).update(
        {PlateLookupCacheEntry.model_variant_id: uuid.uuid4()}
    )
    db_session.commit()
    again = _identify(db_session, s, PLATE)
    assert [r.variant.catalogue_variant_id if r.variant else None for r in again.plate_records] == [named[0], None]


def test_a_wechselschild_whose_stammnummern_the_account_may_not_see_is_still_the_picker(db_session, monkeypatch):
    """`vrn` "depends on user group definition": both cars are offered by their
    Typenschein, the Stammnummer shown as absent, and nothing is cached — a
    record the cache cannot store would otherwise be missing from the next
    answer (one car where there were two)."""

    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle(vrn=None), fix.built_vehicle(vrn=None, fzkey="01911002", typeApprovalNr="1ZZ912", version="2.0 TDI Avant")])}),
    )

    result = _identify(db_session, s, PLATE)

    assert result.outcome is IdentificationOutcome.PLATE_RECORDS
    assert [(r.type_approval_number, r.stammnummer) for r in result.plate_records] == [(fix.DEMO_TYPE_APPROVAL, None), ("1ZZ912", None)]
    assert result.plate_records_conflict is False
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0

    again = _identify(db_session, s, PLATE)
    assert again.outcome is IdentificationOutcome.PLATE_RECORDS and len(again.plate_records) == 2
    assert len(s.fake.api_requests_to(PLATE_PATH)) == 2, "nothing cached, so the provider is asked again"


def test_two_cars_of_which_one_hides_its_stammnummer_are_both_offered_never_one_chosen(db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle(), fix.built_vehicle(vrn=None, fzkey="01911002", typeApprovalNr="1ZZ912", version="2.0 TDI Avant")])}),
    )

    result = _identify(db_session, s, PLATE)

    assert result.outcome is IdentificationOutcome.PLATE_RECORDS and result.variants == []
    assert [r.stammnummer for r in result.plate_records] == [STAMMNUMMER, None]
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0, "whole or not at all"


def test_several_cars_none_with_a_typenschein_is_no_match_not_an_error(client, db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle(vrn=None, typeApprovalNr=None), fix.built_vehicle(vrn=None, fzkey="01911002", typeApprovalNr=None)])}),
    )
    token = create_access_token(
        user_id=s.user.id, tenant_id=s.connection.tenant_id, group_id=uuid.uuid4(), roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )

    response = client.get("/v1/vehicle-identification", params={"q": PLATE}, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "none"


def test_a_plate_nobody_is_registered_under_says_so_and_caches_nothing(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_status={PLATE_PATH: 404}))

    result = _identify(db_session, s, PLATE)

    assert result.outcome is IdentificationOutcome.NONE and result.notes == [IdentificationNote.PLATE_NOT_FOUND]
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0


def test_a_plate_refused_or_unreachable_is_a_note_never_an_error(db_session, monkeypatch):
    fake = FakeAutoi(api_status={PLATE_PATH: 403})
    s = _connected(db_session, monkeypatch, fake)
    assert _identify(db_session, s, PLATE).notes == [IdentificationNote.PLATE_LOOKUP_NOT_ENTITLED]

    fake.api_status.clear()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    result = _identify(db_session, s, PLATE)
    assert result.outcome is IdentificationOutcome.NONE and result.notes == [IdentificationNote.PLATE_LOOKUP_FAILED]


# --- Stammnummer -----------------------------------------------------------------------


def test_a_stammnummer_is_mdm_first_then_the_live_vrn_lookup(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VRN_PATH: fix.body([fix.built_vehicle()])}))
    db_session.add(VehicleMdm(vin="TESTVIN0000000777", vehicle_number="F-000777", stammnummer="900911777"))
    db_session.commit()

    known = _identify(db_session, s, "900.911.777")
    assert known.outcome is IdentificationOutcome.EXISTING_VEHICLE and s.fake.api_requests_to("/vehiclebuild/vrn/900911777") == []

    result = _identify(db_session, s, "900.911.001")
    assert result.kind is IdentifierKind.STAMMNUMMER and result.outcome is IdentificationOutcome.VARIANTS
    assert result.match_method is ConfigurationMatchMethod.STAMMNUMMER
    assert [v.variant_name for v in result.variants] == ["2.0 TDI Avant quattro"]
    assert result.observed.licence_plate is None, "the plate stays out of the observed identity: the advisor typed a Stammnummer"
    assert len(s.fake.api_requests_to(VRN_PATH)) == 1
    assert result.observed.vin == "TESTVIN0000000911", "the registered car's identity, the VIN included"
    # a `vrn` answer proves nothing about what else is registered on the plate: the plate cache stays empty
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0


def test_a_stammnummer_lookup_never_narrows_a_cached_wechselschild(db_session, monkeypatch):
    """Had the `vrn` answer filled the plate cache, the Wechselschild's second
    car would have vanished from the next plate lookup for 30 days."""

    second_car = fix.built_vehicle(vrn="900.911.002", fzkey="01911002", typeApprovalNr="1ZZ912", version="2.0 TDI Avant")
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle(), second_car]), VRN_PATH: fix.body([fix.built_vehicle()])}),
    )
    assert _identify(db_session, s, PLATE).outcome is IdentificationOutcome.PLATE_RECORDS

    by_stammnummer = _identify(db_session, s, STAMMNUMMER)
    assert by_stammnummer.outcome is IdentificationOutcome.VARIANTS

    again = _identify(db_session, s, PLATE)
    assert again.outcome is IdentificationOutcome.PLATE_RECORDS and len(again.plate_records) == 2
    assert IdentificationNote.PLATE_CACHE_HIT in again.notes and len(s.fake.api_requests_to(PLATE_PATH)) == 1


def test_a_stammnummer_the_provider_does_not_know_falls_through_to_the_cache_then_to_none(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle()])}, api_status={VRN_PATH: 404}))

    nothing = _identify(db_session, s, STAMMNUMMER)
    assert nothing.outcome is IdentificationOutcome.NONE and nothing.notes == []

    _identify(db_session, s, PLATE)  # caches plate → Typenschein + Stammnummer
    cached = _identify(db_session, s, STAMMNUMMER)
    assert cached.outcome is IdentificationOutcome.VARIANTS
    assert IdentificationNote.PLATE_CACHE_HIT in cached.notes
    assert len(s.fake.api_requests_to(VRN_PATH)) == 2, "the live rung is asked first every time"


def test_a_refused_or_unreachable_stammnummer_rung_is_noted_and_falls_through(db_session, monkeypatch):
    fake = FakeAutoi(api_status={VRN_PATH: 403})
    s = _connected(db_session, monkeypatch, fake)
    assert _identify(db_session, s, STAMMNUMMER).notes == [IdentificationNote.PROVIDER_NOT_ENTITLED]

    fake.api_status.clear()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    result = _identify(db_session, s, STAMMNUMMER)
    assert result.outcome is IdentificationOutcome.NONE and result.notes == [IdentificationNote.PROVIDER_UNAVAILABLE]
    assert resilience.is_circuit_open(s.connection.id) is False


# --- VIN (KAN-549) ------------------------------------------------------------------------


def _vin_option(db_session, s, *, granted: bool) -> None:
    connection_service.record_probed_entitlement(
        db_session, connection_id=s.connection.id, capability_code="vin", granted=granted
    )


def test_a_vin_without_the_vin_option_sends_nothing_and_says_so(db_session, monkeypatch):
    """KAN-549 exit criteria 1 and 2: the option never probed, or probed as
    not granted (the test licence) → the note, no request, no call-log row."""

    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VIN_PATH: fix.body([fix.built_vehicle()])}))
    unprobed = _identify(db_session, s, VIN)
    _vin_option(db_session, s, granted=False)
    refused = _identify(db_session, s, VIN)

    for result in (unprobed, refused):
        assert result.outcome is IdentificationOutcome.NONE and result.observed.vin == VIN
        assert result.notes == [IdentificationNote.VIN_DECODE_NOT_ENTITLED]
    assert s.fake.api_requests() == [] and _calls(db_session, "vehicle_vin") == 0


def test_a_vin_with_the_vin_option_is_one_vehicle_vin_request_and_the_registered_car(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VIN_PATH: fix.body([fix.built_vehicle()])}))
    _vin_option(db_session, s, granted=True)
    db_session.add(VehicleMdm(vin="WVWZZZTESTV000777", vehicle_number="F-000777"))
    db_session.commit()

    known = _identify(db_session, s, "WVWZZZTESTV000777")
    assert known.outcome is IdentificationOutcome.EXISTING_VEHICLE and s.fake.api_requests() == []

    result = _identify(db_session, s, VIN)
    assert result.kind is IdentifierKind.VIN and result.match_method is ConfigurationMatchMethod.VIN
    assert result.outcome is IdentificationOutcome.VARIANTS
    assert [v.variant_name for v in result.variants] == ["2.0 TDI Avant quattro"]
    assert result.observed.vin == VIN and result.observed.stammnummer == STAMMNUMMER
    (request,) = s.fake.api_requests_to(VIN_PATH)
    assert ("needAutoiKeyIfExists", "true") in request.query
    assert _calls(db_session, "vehicle_vin") == 1 and _calls(db_session, "vehiclebuild") == 0
    # the variant is stored as master data; a VIN answer never fills the plate cache
    assert db_session.get(ModelVariant, result.variants[0].catalogue_variant_id) is not None
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0


def test_a_vin_the_provider_does_not_know_is_the_manual_path_with_the_vin_kept(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_status={VIN_PATH: 404}))
    _vin_option(db_session, s, granted=True)

    result = _identify(db_session, s, VIN)

    assert result.outcome is IdentificationOutcome.NONE and result.notes == []
    assert result.observed.vin == VIN and result.variants == []
    assert _calls(db_session, "vehicle_vin") == 1


def test_a_vin_refused_or_unreachable_at_call_time_is_a_note_never_an_error(db_session, monkeypatch):
    fake = FakeAutoi(api_status={VIN_PATH: 403})
    s = _connected(db_session, monkeypatch, fake)
    _vin_option(db_session, s, granted=True)
    assert _identify(db_session, s, VIN).notes == [IdentificationNote.VIN_DECODE_NOT_ENTITLED]

    fake.api_status.clear()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    result = _identify(db_session, s, VIN)
    assert result.outcome is IdentificationOutcome.NONE and result.observed.vin == VIN
    assert result.notes == [IdentificationNote.PROVIDER_UNAVAILABLE]
    assert resilience.is_circuit_open(s.connection.id) is False


# --- Typenschein / Werkscode -------------------------------------------------------------


def test_a_typenschein_is_one_catalogue_page_merged_with_what_is_stored(db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={TYPENSCHEIN_PATH: fix.body([fix.catalog_variant(), fix.catalog_variant(fzkey="01911002", version="2.0 TDI Avant", edition=None)])}),
    )

    result = _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)

    assert result.kind is IdentifierKind.TYPENSCHEIN and result.outcome is IdentificationOutcome.VARIANTS
    assert sorted(v.variant_name for v in result.variants) == ["2.0 TDI Avant", "2.0 TDI Avant quattro"]
    assert result.best_match_available is True
    assert len(s.fake.api_requests_to(TYPENSCHEIN_PATH)) == 1

    # a repeat asks again (the stored rows are a cache of what was seen, not the source) and duplicates nothing
    again = _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)
    assert len(again.variants) == 2 and db_session.scalar(select(func.count()).select_from(ModelVariant)) == 2


def test_a_typenschein_the_provider_cannot_answer_falls_back_to_the_stored_variants(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={TYPENSCHEIN_PATH: fix.body([fix.catalog_variant()])})
    s = _connected(db_session, monkeypatch, fake)
    _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)  # stores the variant

    fake.api_status[TYPENSCHEIN_PATH] = 403
    refused = _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)
    assert [v.variant_name for v in refused.variants] == ["2.0 TDI Avant quattro"]
    assert refused.notes == [IdentificationNote.PROVIDER_NOT_ENTITLED]

    fake.api_status[TYPENSCHEIN_PATH] = 404
    empty = _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)
    assert len(empty.variants) == 1 and empty.notes == []

    fake.api_status.clear()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    down = _identify(db_session, s, fix.DEMO_TYPE_APPROVAL)
    assert len(down.variants) == 1 and down.notes == [IdentificationNote.PROVIDER_UNAVAILABLE]


def test_a_werkscode_is_live_and_a_tenant_without_a_connection_reads_what_is_stored(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={WERKSCODE_PATH: fix.body([fix.catalog_variant()])}))

    result = _identify(db_session, s, fix.DEMO_WERKSCODE)
    assert result.kind is IdentifierKind.WERKSCODE and [v.variant_name for v in result.variants] == ["2.0 TDI Avant quattro"]
    assert len(s.fake.api_requests_to(WERKSCODE_PATH)) == 1

    other = svc.identify(db_session, tenant_id=uuid.uuid4(), actor_id=None, query=fix.DEMO_WERKSCODE)
    assert [v.variant_name for v in other.variants] == ["2.0 TDI Avant quattro"], "master data is global (ADR-075)"
    assert other.notes == [IdentificationNote.NO_PROVIDER_CONNECTION]


def test_a_werkscode_the_provider_cannot_answer_falls_back_to_the_stored_variants(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={WERKSCODE_PATH: fix.body([fix.catalog_variant()])})
    s = _connected(db_session, monkeypatch, fake)
    _identify(db_session, s, fix.DEMO_WERKSCODE)  # stores the variant

    fake.api_status[WERKSCODE_PATH] = 404
    empty = _identify(db_session, s, fix.DEMO_WERKSCODE)
    assert [v.variant_name for v in empty.variants] == ["2.0 TDI Avant quattro"] and empty.notes == []

    fake.api_status[WERKSCODE_PATH] = 403
    refused = _identify(db_session, s, fix.DEMO_WERKSCODE)
    assert len(refused.variants) == 1 and refused.notes == [IdentificationNote.PROVIDER_NOT_ENTITLED]

    fake.api_status.clear()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    down = _identify(db_session, s, fix.DEMO_WERKSCODE)
    assert len(down.variants) == 1 and down.notes == [IdentificationNote.PROVIDER_UNAVAILABLE]

    unknown = _identify(db_session, s, "ZZ99UNKNOWN")
    assert unknown.kind is IdentifierKind.WERKSCODE and unknown.outcome is IdentificationOutcome.NONE


def test_the_api_carries_the_trim_and_the_notes(client, db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={PLATE_PATH: fix.body([fix.built_vehicle()])}))
    token = create_access_token(
        user_id=s.user.id, tenant_id=s.connection.tenant_id, group_id=uuid.uuid4(), roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )

    response = client.get("/v1/vehicle-identification", params={"q": PLATE}, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["variants"][0]["trimName"] == "Sport" and body["notes"] == []

    s.fake.api_status["/vehiclebuild/licenseplate/ZH911002"] = 403
    refused = client.get("/v1/vehicle-identification", params={"q": "ZH 911 002"}, headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 200 and refused.json()["notes"] == ["plate_lookup_not_entitled"]
