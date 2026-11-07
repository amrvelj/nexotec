"""KAN-547 / KAN-548 — the identAPI adapter's identification and browse
operations, run through the real registered adapter over the fake
authorization server and identAPI (`tests/identapi_support.py`), with the
invented bodies of `tests/identapi_fixtures.py`.

Exit criterion 2 of KAN-547, adapter side: each operation's happy path, an
empty answer (404 and an empty `data` both read as "nothing"), a 403 as
`ProviderNotEntitledError`, a timeout as `ProviderTimeoutError`; each is
one request under its identAPI operation name (KAN-486); a Wechselschild
is two records. ADR-112 (4): a search is one page, the anti-scraping guard
refuses an unscoped query before anything is sent.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest

from app.integration.adapters import identapi_parse as parse
from app.integration.adapters.auto_i_dat_ident import (
    OP_CAPTION_CODE,
    OP_VEHICLEBUILD,
    OP_VEHICLECATALOG,
    OP_VEHICLEMATCH,
    PROVIDER_PAGE_SIZE,
)
from app.integration.errors import ProviderNotEntitledError, ProviderRequestError, ProviderTimeoutError
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.services import resilience
from app.integration.services.gateway import call_capability
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _rows(db_session, connection, operation: str) -> list[IntegrationCallLog]:
    return (
        db_session.query(IntegrationCallLog)
        .filter_by(connection_id=connection.id, capability=operation)
        .order_by(IntegrationCallLog.created_at, IntegrationCallLog.id)
        .all()
    )


def _call(db_session, s, operation, fn):
    with call_capability(db_session, connection=s.connection, capability=operation, actor_id=s.user.id) as adapter:
        return fn(adapter)


# --- parsing: the spec block as the wire carries it ------------------------------------


def test_a_catalogue_row_reads_as_the_full_spec_block_with_zero_as_unknown():
    variant = parse.parse_variant(fix.catalog_variant())
    assert variant is not None
    assert (variant.fz_key, variant.brand_display_name, variant.model_group_name) == (fix.DEMO_FZ_KEY, "Testmarke", "Prüfling")
    assert variant.variant_name == "2.0 TDI Avant quattro"
    assert variant.model_type_name == "Testmarke Prüfling 2.0 TDI Avant quattro"
    assert variant.trim_name == "Sport"
    # raw codes, never interpreted
    assert (variant.vehicle_kind_code, variant.fuel_type_code, variant.body_style_code) == ("01", "3", "5")
    assert (variant.drivetrain_code, variant.transmission_code, variant.vehicle_class_code) == ("5", "5", "4")
    assert (variant.emission_standard_code, variant.consumption_norm_code, variant.energy_label) == ("6d", "4", "B")
    # figures
    assert (variant.displacement_ccm, variant.cylinders, variant.gears, variant.ps, variant.kw) == (1968, 4, 7, 204, 150)
    assert (variant.doors, variant.seats, variant.weight_empty_kg, variant.weight_total_kg) == (5, 5, 1790, 2310)
    assert (variant.payload_kg, variant.towing_capacity_kg, variant.wheelbase_mm) == (520, 2000, 2820)
    assert variant.consumption_mixed == Decimal("5.4")
    assert (variant.co2_gkm, variant.tank_capacity_l) == (142, 58)
    # 0 means unknown → None, never 0 (PRD §Data specification)
    assert variant.total_ps is None and variant.total_kw is None and variant.system_kw is None
    assert variant.consumption_urban is None and variant.range_km is None and variant.battery_capacity_kwh is None
    # production window and price
    assert (variant.production_from, variant.production_to) == (202103, None)
    assert (variant.model_year_from, variant.model_year_to) == (2021, None)
    assert variant.base_price == Decimal(58900) and variant.base_price_year == 2023 and variant.price_is_net is False
    assert [(p.year, p.price) for p in variant.catalogue_prices] == [(2021, 56900), (2022, 57900), (2023, 58900)]
    assert variant.type_approval_numbers == [fix.DEMO_TYPE_APPROVAL, "1ZZ912"]
    assert (variant.werkscode, variant.importcode, variant.model_group_key) == (fix.DEMO_WERKSCODE, "IMP911", fix.DEMO_MODEL_GROUP_KEY)


def test_a_row_without_an_fzkey_is_no_variant_and_a_built_vehicle_keeps_its_registration():
    assert parse.parse_variant(fix.catalog_variant(fzkey=None)) is None
    built = parse.parse_built_vehicle(fix.built_vehicle(fzkey=None, productionEnd="2024-06-30T00:00:00"))
    assert built.variant is None and built.fz_key is None
    assert (built.vin, built.stammnummer, built.plate) == ("TESTVIN0000000911", "900911001", "ZH911001")
    assert built.first_registration_date == dt.date(2022, 5, 17) and built.model_year == 2022
    assert (built.type_approval_number, built.production_from, built.production_to) == (fix.DEMO_TYPE_APPROVAL, 2021, 2024)


# --- identification operations -----------------------------------------------------------


def test_a_plate_lookup_is_one_vehiclebuild_request_and_a_wechselschild_is_two_records(db_session, monkeypatch):
    fake = FakeAutoi(
        api_bodies={
            "/vehiclebuild/licenseplate/ZH911001": fix.body(
                [fix.built_vehicle(), fix.built_vehicle(vrn="900.911.002", fzkey="03911002", type="03", brand="Zweirad", typeApprovalNr="3ZZ001")]
            )
        }
    )
    s = fx.connected(db_session, monkeypatch, fake=fake)

    vehicles = _call(db_session, s, "kontrollschild", lambda a: a.identify_by_plate("ZH911001"))

    assert [v.stammnummer for v in vehicles] == ["900911001", "900911002"]
    assert vehicles[0].variant is not None and vehicles[0].variant.fz_key == fix.DEMO_FZ_KEY
    assert vehicles[1].vehicle_kind_code == "03"
    (request,) = fake.api_requests_to("/vehiclebuild/licenseplate/ZH911001")
    assert ("needAutoiKeyIfExists", "true") in request.query
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLEBUILD)] == [CallStatus.SUCCESS]


def test_a_stammnummer_lookup_asks_vrn_and_an_empty_answer_is_no_match(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={"/vehiclebuild/vrn/900911001": fix.body([fix.built_vehicle()])})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    found = _call(db_session, s, "stammnummer", lambda a: a.identify_by_vrn("900911001"))
    nothing = _call(db_session, s, "stammnummer", lambda a: a.identify_by_vrn("900911009"))

    assert [v.plate for v in found] == ["ZH911001"]
    assert nothing == []
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLEBUILD)] == [CallStatus.SUCCESS] * 2


def test_a_404_reads_as_nothing_found_not_as_an_error(db_session, monkeypatch):
    fake = FakeAutoi(api_status={"/vehiclebuild/vrn/900911009": 404, "/vehiclecatalog/typeapproval/1ZZ999": 404})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    assert _call(db_session, s, "stammnummer", lambda a: a.identify_by_vrn("900911009")) == []
    assert _call(db_session, s, "typenschein", lambda a: a.find_variants_by_type_approval("1ZZ999")) == []
    assert all(r.status == CallStatus.SUCCESS for r in _rows(db_session, s.connection, OP_VEHICLEBUILD))
    assert resilience.is_circuit_open(s.connection.id) is False


def test_a_typenschein_lookup_is_one_page_of_the_catalogue(db_session, monkeypatch):
    path = f"/vehiclecatalog/typeapproval/{fix.DEMO_TYPE_APPROVAL}"
    fake = FakeAutoi(api_bodies={path: fix.body([fix.catalog_variant(), fix.catalog_variant(fzkey="01911002", version="2.0 TDI Avant")], total=12)})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    variants = _call(db_session, s, "typenschein", lambda a: a.find_variants_by_type_approval(fix.DEMO_TYPE_APPROVAL))

    assert [v.fz_key for v in variants] == [fix.DEMO_FZ_KEY, "01911002"]
    (request,) = fake.api_requests_to(path)
    assert ("page[size]", str(PROVIDER_PAGE_SIZE)) in request.query and ("page[number]", "1") in request.query
    assert len(fake.api_requests_to(path)) == 1, "no second page is ever fetched (ADR-112 (4))"
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLECATALOG)] == [CallStatus.SUCCESS]


def test_a_werkscode_lookup_and_a_variant_read_use_the_catalogue_identifier_paths(db_session, monkeypatch):
    fake = FakeAutoi(
        api_bodies={
            f"/vehiclecatalog/factorycode/{fix.DEMO_WERKSCODE}": fix.body([fix.catalog_variant()]),
            f"/vehiclecatalog/autoi/{fix.DEMO_FZ_KEY}": fix.body([fix.catalog_variant()]),
        }
    )
    s = fx.connected(db_session, monkeypatch, fake=fake)

    by_code = _call(db_session, s, "werkscode", lambda a: a.find_variants_by_werkscode(fix.DEMO_WERKSCODE))
    one = _call(db_session, s, "vehicle_data", lambda a: a.fetch_variant(fix.DEMO_FZ_KEY))
    none = _call(db_session, s, "vehicle_data", lambda a: a.fetch_variant("01999999"))

    assert [v.fz_key for v in by_code] == [fix.DEMO_FZ_KEY]
    assert one is not None and one.fz_key == fix.DEMO_FZ_KEY
    assert none is None


def test_a_403_is_not_entitled_and_a_timeout_is_unavailable(db_session, monkeypatch):
    fake = FakeAutoi(api_status={"/vehiclebuild/licenseplate/ZH911001": 403})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    with pytest.raises(ProviderNotEntitledError):
        _call(db_session, s, "kontrollschild", lambda a: a.identify_by_plate("ZH911001"))
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLEBUILD)] == [CallStatus.ERROR]
    assert resilience.is_circuit_open(s.connection.id) is False, "a 403 is the provider's coherent answer"

    fake.raise_on_api = httpx.ReadTimeout("slow")
    with pytest.raises(ProviderTimeoutError):
        _call(db_session, s, "stammnummer", lambda a: a.identify_by_vrn("900911001"))
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLEBUILD)][1:] == [CallStatus.TIMEOUT, CallStatus.TIMEOUT]


def test_the_best_match_is_the_vehiclematch_typeapproval_path_and_an_unmatched_answer_is_no_match(db_session, monkeypatch):
    path = f"/vehiclematch/typeapproval/{fix.DEMO_TYPE_APPROVAL}/58900"
    fake = FakeAutoi(api_bodies={path: fix.match_body(2)})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    result = _call(
        db_session, s, "vehicle_data",
        lambda a: a.find_best_match(typ_sch_nr=fix.DEMO_TYPE_APPROVAL, neupreis=58900, modell_bez="Avant", tueren=5),
    )
    assert result.match_code == 2 and result.vehicle.fz_key == fix.DEMO_FZ_KEY
    (request,) = fake.api_requests_to(path)
    assert ("version", "Avant") in request.query and ("doors", "5") in request.query
    assert [r.status for r in _rows(db_session, s.connection, OP_VEHICLEMATCH)] == [CallStatus.SUCCESS]

    fake.api_bodies[path] = fix.match_body(0)
    with pytest.raises(KeyError):
        _call(db_session, s, "vehicle_data", lambda a: a.find_best_match(typ_sch_nr=fix.DEMO_TYPE_APPROVAL, neupreis=58900))


def test_captions_are_read_per_language_as_group_kind_code(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={"/caption/code": fix.captions_body("fr")})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    captions = _call(db_session, s, OP_CAPTION_CODE, lambda a: a.fetch_captions("fr"))

    assert {(c.group, c.kind, c.code, c.label) for c in captions} == {
        ("type", "global", "01", "Personenwagen (fr)"),
        ("type", "global", "02", "Nutzfahrzeug (fr)"),
        ("fuel", "passenger", "3", "Diesel (fr)"),
        ("fuel", "moto", "3", "Bleifrei Kat. (fr)"),
        ("gearbox", "passenger", "5", "Automat (fr)"),
    }
    (request,) = [r for r in fake.api_requests_to("/caption/code") if ("lang", "fr") in r.query]
    assert request.path == "/caption/code"


# --- catalogue browse operations (KAN-548) ------------------------------------------------


def test_brands_and_model_groups_are_read_per_vehicle_type(db_session, monkeypatch):
    fake = FakeAutoi(
        api_bodies={
            "/vehiclecatalog/brand/passenger": fix.brands_body(),
            "/vehiclecatalog/modelgroup/passenger/Testmarke": fix.model_groups_body(),
        }
    )
    s = fx.connected(db_session, monkeypatch, fake=fake)

    brands = _call(db_session, s, "catalogue", lambda a: a.list_catalogue_brands(vehicle_kind_code="01"))
    groups = _call(
        db_session, s, "catalogue",
        lambda a: a.list_catalogue_model_groups(vehicle_kind_code="01", brand="Testmarke", in_production=True),
    )

    assert [(b.name, b.brand_nr, b.models) for b in brands] == [("Testmarke", "911", ["Prüfling", "Muster"]), ("Zweitmarke", "912", ["Beispiel"])]
    assert [(g.model_group_key, g.production_year_from, g.production_year_to) for g in groups] == [
        (fix.DEMO_MODEL_GROUP_KEY, 2021, None), ("104912", 2015, 2020),
    ]
    (request,) = fake.api_requests_to("/vehiclecatalog/modelgroup/passenger/Testmarke")
    assert ("onlyInProduction", "true") in request.query
    with pytest.raises(ProviderRequestError):
        _call(db_session, s, "catalogue", lambda a: a.list_catalogue_brands(vehicle_kind_code="09"))


def test_a_variant_search_is_exactly_the_page_asked_for_with_its_count(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={"/vehiclecatalog": fix.body([fix.catalog_variant()], total=23)})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    page = _call(
        db_session, s, "catalogue",
        lambda a: a.search_catalogue_variants(
            vehicle_kind_code="01", brand="Testmarke", exfilters=["eq(fuel,'3')", "ge(kw,'100')"], sort="kw desc", page_number=3,
        ),
    )

    assert (page.page_number, page.page_size, page.total) == (3, PROVIDER_PAGE_SIZE, 23)
    assert [v.fz_key for v in page.items] == [fix.DEMO_FZ_KEY]
    (request,) = fake.api_requests_to("/vehiclecatalog")
    assert ("filter[type]", "01") in request.query and ("filter[brand]", "Testmarke") in request.query
    assert [v for k, v in request.query if k == "exfilter"] == ["eq(fuel,'3')", "ge(kw,'100')"]
    assert ("sort", "kw desc") in request.query and ("includeCount", "true") in request.query
    assert ("page[number]", "3") in request.query and ("page[size]", str(PROVIDER_PAGE_SIZE)) in request.query
    assert len(fake.api_requests_to("/vehiclecatalog")) == 1, "one page, one request (ADR-112 (4))"


def test_a_model_group_scope_uses_the_model_group_path(db_session, monkeypatch):
    path = f"/vehiclecatalog/modelgroup/{fix.DEMO_MODEL_GROUP_KEY}"
    fake = FakeAutoi(api_bodies={path: fix.body([fix.catalog_variant()])})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    page = _call(
        db_session, s, "catalogue",
        lambda a: a.search_catalogue_variants(vehicle_kind_code="01", model_group_key=fix.DEMO_MODEL_GROUP_KEY),
    )

    assert [v.fz_key for v in page.items] == [fix.DEMO_FZ_KEY] and page.total == 1
    assert len(fake.api_requests_to(path)) == 1


def test_an_unscoped_search_or_facet_is_refused_before_anything_is_sent(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch)
    sent_before = len(s.fake.api_requests())

    with pytest.raises(ProviderRequestError, match="brand or a model group"):
        _call(db_session, s, "catalogue", lambda a: a.search_catalogue_variants(vehicle_kind_code="01"))
    with pytest.raises(ProviderRequestError):
        _call(db_session, s, "catalogue", lambda a: a.facet_values(field="fuel", vehicle_kind_code="01", brand=""))

    assert len(s.fake.api_requests()) == sent_before
    assert _rows(db_session, s.connection, OP_VEHICLECATALOG) == []


def test_facet_values_are_one_distinct_request_scoped_to_the_brand(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={"/vehiclecatalog": {"data": [{"fuel": "3"}, {"fuel": "4"}, {"fuel": "3"}], "meta": {}}})
    s = fx.connected(db_session, monkeypatch, fake=fake)

    values = _call(db_session, s, "catalogue", lambda a: a.facet_values(field="fuel", vehicle_kind_code="01", brand="Testmarke"))

    assert values == ["3", "4"]
    (request,) = fake.api_requests_to("/vehiclecatalog")
    assert ("fields", "fuel distinct") in request.query and ("filter[brand]", "Testmarke") in request.query
