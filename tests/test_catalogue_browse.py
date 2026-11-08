"""FR-C-01 catalogue browse as **live identAPI queries** (ADR-112, KAN-548;
exit criterion 2) through the real registered adapter over the fake
transport (`tests/identapi_support.py`, bodies from `tests/identapi_fixtures.py`).

Drill-down (vehicle kind → brand → model group → variant), paging (page 2
is a new request with `page[number]=2`), facets scoped to the brand, the
user's filters as `exfilter` expressions on the provider's own codes, a
403 → `notInContract`, an outage → `unavailable`, a Betrieb without a
connection → `notConfigured` — each a 200 with the state, never an error.
Every variant a page returns is stored once; a second browse of the page
asks the provider again and duplicates nothing.
"""

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import create_access_token
from app.integration.services import resilience
from app.vehicle.models.catalogue import ModelVariant
from app.vehicle.models.provider import MappingGap
from app.vehicle.schemas.catalogue import CatalogueBrowseMode, CatalogueBrowseState
from app.vehicle.services import catalogue_browse
from app.vehicle.services.catalogue_browse import BrowseSort, VariantFilters
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi
from tests.test_provider_code_map_seed import _seed_migration

KIND = "passenger_car"
BRAND = "Testmarke"
VARIANTS_PATH = "/vehiclecatalog"
BRANDS_PATH = "/vehiclecatalog/brand/passenger"
GROUPS_PATH = f"/vehiclecatalog/modelgroup/passenger/{BRAND}"
GROUP_VARIANTS_PATH = f"/vehiclecatalog/modelgroup/{fix.DEMO_MODEL_GROUP_KEY}"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _connected(db_session, monkeypatch, fake: FakeAutoi | None = None):
    s = fx.connected(db_session, monkeypatch, fake=fake or FakeAutoi())
    _seed_migration()._seed(db_session.connection())
    db_session.commit()
    return s


def _two_variants():
    return fix.body([fix.catalog_variant(), fix.catalog_variant(fzkey="01911002", version="2.0 TDI Avant", edition=None, fuel="1")])


def _browse(db_session, s, **over):
    args = {
        "tenant_id": s.connection.tenant_id, "actor_id": s.user.id, "vehicle_kind": KIND, "brand": BRAND,
        "model_group_key": None, "filters": VariantFilters(), "sort": [], "page": 1,
    }
    args.update(over)
    return catalogue_browse.browse_variants(db_session, **args)


def _bearer(tenant_id: uuid.UUID, user_id: uuid.UUID | None = None) -> dict[str, str]:
    """The acting user must be a member of the Betrieb: auto-i enrols the
    signed-in user per connection (User Enrollment Grant, KAN-488)."""

    token = create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(), roles=frozenset(), is_dealer_manager=False
    )
    return {"Authorization": f"Bearer {token}"}


# --- drill-down ------------------------------------------------------------------------


def test_brands_and_model_groups_come_from_the_provider_for_the_vehicle_kind(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={BRANDS_PATH: fix.brands_body(), GROUPS_PATH: fix.model_groups_body()}))

    brands = catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND)
    assert brands.state is CatalogueBrowseState.OK
    assert [(b.name, b.models) for b in brands.items] == [("Testmarke", ["Prüfling", "Muster"]), ("Zweitmarke", ["Beispiel"])]

    groups = catalogue_browse.list_model_groups(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, mode=CatalogueBrowseMode.BUILD,
    )
    assert [(g.model_group_key, g.description, g.production_year_to) for g in groups.items] == [
        (fix.DEMO_MODEL_GROUP_KEY, "Prüfling (2021-)", None), ("104912", "Muster (2015-2020)", 2020),
    ]
    (request,) = s.fake.api_requests_to(GROUPS_PATH)
    assert ("onlyInProduction", "true") in request.query, "build mode lists what is in production (PRD NurNeue)"

    catalogue_browse.list_model_groups(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, mode=CatalogueBrowseMode.RECORD,
    )
    assert ("onlyInProduction", "true") not in s.fake.api_requests_to(GROUPS_PATH)[-1].query


def test_a_vehicle_kind_the_provider_map_does_not_know_is_422_and_nothing_is_sent(db_session, monkeypatch):
    from app.core.errors import UnprocessableEntityError

    s = _connected(db_session, monkeypatch)
    with pytest.raises(UnprocessableEntityError):
        catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind="hovercraft")
    assert s.fake.api_requests_to(BRANDS_PATH) == []


def test_variants_of_a_brand_are_one_provider_page_stored_once(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VARIANTS_PATH: _two_variants()}))

    result = _browse(db_session, s)

    assert result.state is CatalogueBrowseState.OK
    assert sorted(v.name for v in result.rows) == ["2.0 TDI Avant", "2.0 TDI Avant quattro"]
    assert result.total == 2 and result.next_page is None
    (request,) = s.fake.api_requests_to(VARIANTS_PATH)
    assert ("filter[brand]", BRAND) in request.query and ("filter[type]", "01") in request.query
    # stored as master data with the block
    stored = db_session.scalars(select(ModelVariant)).all()
    assert len(stored) == 2 and {v.fuel_type for v in stored} == {"diesel", "petrol"} and all(v.doors == 5 for v in stored)

    again = _browse(db_session, s)
    assert len(s.fake.api_requests_to(VARIANTS_PATH)) == 2, "no mirror: the page is asked for again"
    assert db_session.scalar(select(func.count()).select_from(ModelVariant)) == 2, "and nothing is duplicated"
    assert {v.id for v in again.rows} == {v.id for v in result.rows}


def test_a_model_group_scope_queries_the_group_endpoint(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={GROUP_VARIANTS_PATH: fix.body([fix.catalog_variant()])}))

    result = _browse(db_session, s, brand=None, model_group_key=fix.DEMO_MODEL_GROUP_KEY)

    assert [v.name for v in result.rows] == ["2.0 TDI Avant quattro"]
    (request,) = s.fake.api_requests_to(GROUP_VARIANTS_PATH)
    assert ("filter[type]", "01") in request.query and not any(k == "filter[brand]" for k, _ in request.query)


# --- paging ----------------------------------------------------------------------------


def test_page_two_is_a_new_request_with_the_provider_page_number(db_session, monkeypatch):
    ten = fix.body([fix.catalog_variant(fzkey=f"0191{n:04d}") for n in range(10)], total=23)
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VARIANTS_PATH: ten}))

    first = _browse(db_session, s, page=1)
    assert (first.total, first.next_page, len(first.rows)) == (23, 2, 10)

    second = _browse(db_session, s, page=2)
    assert second.next_page == 3
    requests = s.fake.api_requests_to(VARIANTS_PATH)
    assert [dict(r.query)["page[number]"] for r in requests] == ["1", "2"]
    assert all(dict(r.query)["page[size]"] == "10" for r in requests)

    last = _browse(db_session, s, page=3)
    assert last.next_page is None, "page 3 holds the 21st to 23rd variant; nothing follows"


# --- filters, sort, facets -------------------------------------------------------------


def test_filters_become_exfilter_expressions_on_the_providers_own_codes(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch)

    _browse(
        db_session, s,
        filters=VariantFilters(coded={"fuelType": "diesel", "transmission": "automatic"}, numeric_min={"kw": 100}, numeric_max={"doors": 5}, q="Avant"),
        sort=[BrowseSort("kw", True), BrowseSort("doors", False)],
    )

    (request,) = s.fake.api_requests_to(VARIANTS_PATH)
    expressions = [v for k, v in request.query if k == "exfilter"]
    assert "in(fuel,'3','4')" in expressions, "diesel maps from two auto-i codes for a passenger car — both are matched, none written here"
    assert any(e.startswith("eq(gearbox,'") for e in expressions), "a value with one provider code is an equality"
    assert "ge(kw,'100')" in expressions and "le(doors,'5')" in expressions
    assert "has(descriptionShort,'Avant')" in expressions
    assert ("sort", "kw desc,doors") in request.query


def test_a_filter_value_the_provider_map_does_not_know_is_422_before_anything_is_sent(db_session, monkeypatch):
    """The user's 422 is decided before the gateway block: no request, no
    call-log row, and the breaker is not charged — five typos must not switch
    the Betrieb's connection off (identification included)."""

    from app.core.errors import UnprocessableEntityError
    from app.integration.models.call_log import IntegrationCallLog

    s = _connected(db_session, monkeypatch)
    for _ in range(5):
        with pytest.raises(UnprocessableEntityError) as refused:
            _browse(db_session, s, filters=VariantFilters(coded={"fuelType": "nonsense"}))
        assert refused.value.details == {"reason": "catalogue_filter_value_unknown", "invalid": {"fuelType": "nonsense"}}
    for _ in range(5):
        with pytest.raises(UnprocessableEntityError):
            catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind="trailer")
    assert s.fake.api_requests_to(VARIANTS_PATH) == [] and s.fake.api_requests_to(BRANDS_PATH) == []
    assert db_session.scalar(select(func.count()).select_from(IntegrationCallLog)) == 0, "no row for a request never sent"
    assert resilience.is_circuit_open(s.connection.id) is False

    ok = _browse(db_session, s)
    assert ok.state is CatalogueBrowseState.OK, "the connection still answers after the user's mistakes"


def test_a_brand_or_model_group_key_that_is_not_a_name_is_422_and_never_reaches_the_provider_path(db_session, monkeypatch):
    """`brand` and `modelGroupKey` end up in the identAPI's path: a «..», a
    slash or a query character is refused here, and the adapter percent-encodes
    what it sends — never another identAPI path on the dealer's credentials."""

    from app.core.errors import UnprocessableEntityError

    s = _connected(db_session, monkeypatch)
    for bad_brand in ("../../vehiclebuild/vrn/123456789", "a/b", "x?y", "100%", "name#1", "..", ".", "..."):
        with pytest.raises(UnprocessableEntityError) as refused:
            _browse(db_session, s, brand=bad_brand)
        assert refused.value.details["reason"] == "catalogue_scope_invalid"
        with pytest.raises(UnprocessableEntityError):
            catalogue_browse.list_model_groups(
                db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=bad_brand,
                mode=CatalogueBrowseMode.RECORD,
            )
    for bad_key in ("..", "../x", "104 911", "k/1", "a" * 33):
        with pytest.raises(UnprocessableEntityError) as refused:
            _browse(db_session, s, brand=None, model_group_key=bad_key)
        assert refused.value.details["reason"] == "catalogue_scope_invalid"
    assert s.fake.api_requests() == []

    # a brand with a space is a name: sent percent-encoded in the path
    from app.integration.adapters.auto_i_dat_ident import AutoIDatIdentAdapter

    sent_paths: list[str] = []
    original_request = AutoIDatIdentAdapter._request

    def _recording_request(self, operation, method, path, *args, **kwargs):
        sent_paths.append(path)
        return original_request(self, operation, method, path, *args, **kwargs)

    monkeypatch.setattr(AutoIDatIdentAdapter, "_request", _recording_request)
    catalogue_browse.list_model_groups(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand="Alfa Romeo",
        mode=CatalogueBrowseMode.RECORD,
    )
    (request,) = [r for r in s.fake.api_requests() if r.path.startswith("/vehiclecatalog/modelgroup/")]
    assert request.path == "/vehiclecatalog/modelgroup/passenger/Alfa Romeo"
    assert sent_paths == ["/vehiclecatalog/modelgroup/passenger/Alfa%20Romeo"], "the adapter sends the segment percent-encoded"


def test_facets_are_the_providers_distinct_values_scoped_to_the_brand_resolved_to_canonical_codes(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VARIANTS_PATH: fix.body([{"fuel": "3"}, {"fuel": "1"}, {"fuel": "77"}])}))

    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
    )

    assert facets.state is CatalogueBrowseState.OK
    assert facets.coded["fuelType"] == ["diesel", "petrol"], "code 77 has no map row: a gap, never offered"
    gap = db_session.scalar(select(MappingGap).where(MappingGap.code_group == "fuel_type", MappingGap.provider_code == "77"))
    assert gap is not None, "an unmapped facet value is a mapping gap (FR-C-10)"
    for request in s.fake.api_requests_to(VARIANTS_PATH):
        assert ("filter[brand]", BRAND) in request.query


# --- states, never errors --------------------------------------------------------------


def test_a_refused_query_is_not_in_contract(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_status={VARIANTS_PATH: 403, BRANDS_PATH: 403}))

    result = _browse(db_session, s)
    assert result.state is CatalogueBrowseState.NOT_IN_CONTRACT and result.rows == [] and result.total is None
    brands = catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND)
    assert brands.state is CatalogueBrowseState.NOT_IN_CONTRACT and brands.items == []
    assert resilience.is_circuit_open(s.connection.id) is False, "the provider's coherent refusal never charges the breaker"


def test_an_ungranted_ident_basis_option_is_not_in_contract_without_a_request(db_session, monkeypatch):
    """The probed entitlement row says the option is not licensed: the browse
    answers `notInContract` from the row, sending nothing."""

    import datetime as dt

    from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement

    s = _connected(db_session, monkeypatch)
    db_session.add(
        IntegrationEntitlement(
            connection_id=s.connection.id, capability_code="ident_basis", granted=False,
            source=EntitlementSource.PROBED, checked_at=dt.datetime.now(dt.UTC),
        )
    )
    db_session.commit()

    assert _browse(db_session, s).state is CatalogueBrowseState.NOT_IN_CONTRACT
    brands = catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND)
    assert brands.state is CatalogueBrowseState.NOT_IN_CONTRACT
    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
    )
    assert facets.state is CatalogueBrowseState.NOT_IN_CONTRACT
    assert s.fake.api_requests() == []


def test_an_outage_is_unavailable_and_the_facet_panel_stops_at_the_first_failure(db_session, monkeypatch):
    fake = FakeAutoi()
    fake.raise_on_api = httpx.ReadTimeout("slow")
    s = _connected(db_session, monkeypatch, fake)

    result = _browse(db_session, s)
    assert result.state is CatalogueBrowseState.UNAVAILABLE and result.rows == []
    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
    )
    assert facets.state is CatalogueBrowseState.UNAVAILABLE and facets.coded == {}
    # one screen (a page + the panel) charges the breaker twice, not five times: the panel stops at its
    # first failure (the adapter retries a timeout once, so each block is two requests on the wire)
    assert len(s.fake.api_requests_to(VARIANTS_PATH)) == 4
    assert resilience.is_circuit_open(s.connection.id) is False


def test_a_facet_panel_whose_later_field_fails_keeps_the_fields_answered(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={VARIANTS_PATH: fix.body([{"fuel": "3"}])})
    s = _connected(db_session, monkeypatch, fake)
    calls = {"n": 0}
    original = fake._ident_api

    def _third_call_fails(request):
        calls["n"] += 1
        if calls["n"] in (3, 4):  # the third field, and the adapter's one retry of it
            raise httpx.ReadTimeout("slow")
        return original(request)

    monkeypatch.setattr(fake, "_ident_api", _third_call_fails)
    facets = catalogue_browse.compute_facets(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
    )
    assert facets.state is CatalogueBrowseState.UNAVAILABLE
    assert list(facets.coded) == ["fuelType", "bodyStyle"], "the two fields answered before the failure stay offered"


def test_a_betrieb_without_a_connection_is_not_configured(db_session, monkeypatch):
    _connected(db_session, monkeypatch)
    contract_less = uuid.uuid4()
    result = catalogue_browse.browse_variants(
        db_session, tenant_id=contract_less, actor_id=None, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
        filters=VariantFilters(), sort=[], page=1,
    )
    assert result.state is CatalogueBrowseState.NOT_CONFIGURED
    brands = catalogue_browse.list_brands(db_session, tenant_id=contract_less, actor_id=None, vehicle_kind=KIND)
    assert brands.state is CatalogueBrowseState.NOT_CONFIGURED and brands.items == []


# --- the endpoints ---------------------------------------------------------------------


def test_endpoints_return_the_brands_the_grid_the_facets_and_the_model_groups(client, db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={BRANDS_PATH: fix.brands_body(), GROUPS_PATH: fix.model_groups_body(), VARIANTS_PATH: _two_variants()}),
    )
    headers = _bearer(s.connection.tenant_id, s.user.id)

    brands = client.get("/v1/catalogue/brands?vehicleKind=passenger_car", headers=headers)
    assert brands.status_code == 200, brands.text
    assert brands.json()["state"] == "ok" and [b["name"] for b in brands.json()["items"]] == ["Testmarke", "Zweitmarke"]

    groups = client.get(f"/v1/catalogue/model-groups?brand={BRAND}&mode=record", headers=headers)
    assert groups.status_code == 200, groups.text
    assert groups.json()["items"][0] == {
        "key": fix.DEMO_MODEL_GROUP_KEY, "name": "Prüfling (2021-)", "modelSearch": "PRUEFLING",
        "productionYearFrom": 2021, "productionYearTo": None,
    }

    variants = client.get(f"/v1/catalogue/variants?brand={BRAND}&sort=kw:desc&kwMin=100", headers=headers)
    assert variants.status_code == 200, variants.text
    body = variants.json()
    assert body["state"] == "ok" and body["browseAvailable"] is True and body["total"] == 2 and body["nextCursor"] is None
    assert sorted(v["variantName"] for v in body["items"]) == ["2.0 TDI Avant", "2.0 TDI Avant quattro"]
    assert body["items"][0]["spec"]["ps"] == 204 and Decimal(body["items"][0]["currentPrice"]["amount"]) == 58900

    page_two = client.get(f"/v1/catalogue/variants?brand={BRAND}&cursor=2", headers=headers)
    assert page_two.status_code == 200 and ("page[number]", "2") in s.fake.api_requests_to(VARIANTS_PATH)[-1].query

    facets = client.get(f"/v1/catalogue/facets?brand={BRAND}", headers=headers)
    assert facets.status_code == 200, facets.text
    assert facets.json()["state"] == "ok" and facets.json()["numeric"] == {}
    assert [f["valueCode"] for f in facets.json()["coded"]["fuelType"]] == ["diesel", "petrol"]
    assert facets.json()["coded"]["fuelType"][0]["count"] is None


def test_the_endpoint_states_are_200_answers(client, db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_status={VARIANTS_PATH: 403}))

    refused = client.get(f"/v1/catalogue/variants?brand={BRAND}", headers=_bearer(s.connection.tenant_id, s.user.id))
    assert refused.status_code == 200 and refused.json()["state"] == "notInContract" and refused.json()["items"] == []

    no_connection = client.get(f"/v1/catalogue/variants?brand={BRAND}", headers=_bearer(uuid.uuid4()))
    assert no_connection.status_code == 200 and no_connection.json()["state"] == "notConfigured"
    assert no_connection.json()["browseAvailable"] is False


def test_an_unscoped_query_an_unknown_sort_and_a_bad_cursor_are_422(client, db_session, monkeypatch):
    s = _connected(db_session, monkeypatch)
    headers = _bearer(s.connection.tenant_id, s.user.id)
    assert client.get("/v1/catalogue/variants", headers=headers).status_code == 422
    assert client.get(f"/v1/catalogue/variants?brand={BRAND}&sort=ps:desc", headers=headers).status_code == 422
    assert client.get(f"/v1/catalogue/variants?brand={BRAND}&cursor=abc", headers=headers).status_code == 422
    assert s.fake.api_requests_to(VARIANTS_PATH) == []


def test_a_stored_variant_is_read_by_id_without_a_connection(client, db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={VARIANTS_PATH: fix.body([fix.catalog_variant()])}))
    (variant,) = _browse(db_session, s).rows

    read = client.get(f"/v1/catalogue/variants/{variant.id}", headers=_bearer(uuid.uuid4()))
    assert read.status_code == 200 and read.json()["variantName"] == "2.0 TDI Avant quattro"
    assert len(s.fake.api_requests_to(VARIANTS_PATH)) == 1, "a stored row, no provider call"
