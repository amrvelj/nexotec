"""ADR-112 (KAN-548) — catalogue browse **is** a live provider query, and a
bounded one: a browse page is exactly one provider request, a facet panel
exactly one request per facet field, a brand list and a model-group list
one each; nothing is fetched for a page or a field the user did not ask
for. Replaces `test_catalogue_browse_makes_no_provider_call.py` (C-B,
KAN-40), which asserted the opposite for the mirror KAN-485 removed.

Through the real registered identAPI adapter over the fake transport
(`tests/identapi_support.py`), counting the requests the fake saw and the
call-log rows the gateway wrote (KAN-486: one row per request).
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.integration.models.call_log import IntegrationCallLog
from app.integration.services import resilience
from app.vehicle.schemas.catalogue import CatalogueBrowseMode, CatalogueBrowseState
from app.vehicle.services import catalogue_browse
from app.vehicle.services.catalogue_browse import FACET_FIELDS, BrowseSort, VariantFilters
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi
from tests.test_provider_code_map_seed import _seed_migration

KIND = "passenger_car"
BRAND = "Testmarke"


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


def _catalogue_requests(fake: FakeAutoi) -> list:
    return [r for r in fake.api_requests() if r.path.startswith("/vehiclecatalog")]


def _call_log_rows(db_session) -> int:
    return db_session.scalar(
        select(func.count()).select_from(IntegrationCallLog).where(IntegrationCallLog.capability == "vehiclecatalog")
    )


def test_one_browse_page_is_exactly_one_provider_request(db_session, monkeypatch):
    many = fix.body([fix.catalog_variant(fzkey=f"0191{n:04d}") for n in range(10)], total=37)
    s = _connected(db_session, monkeypatch, FakeAutoi(api_bodies={"/vehiclecatalog": many}))

    result = catalogue_browse.browse_variants(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND,
        model_group_key=None, filters=VariantFilters(), sort=[BrowseSort("kw", True)], page=2,
    )

    assert result.state is CatalogueBrowseState.OK and len(result.rows) == 10
    assert (result.total, result.next_page) == (37, 3)
    requests = _catalogue_requests(s.fake)
    assert len(requests) == 1, "one page asked, one request sent — never the next page"
    (request,) = requests
    assert ("page[number]", "2") in request.query and ("page[size]", "10") in request.query
    assert ("filter[brand]", BRAND) in request.query and ("includeCount", "true") in request.query
    assert _call_log_rows(db_session) == 1


def test_the_facet_panel_is_one_request_per_facet_field_and_nothing_before_a_brand(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi())
    scope = {"tenant_id": s.connection.tenant_id, "actor_id": s.user.id, "vehicle_kind": KIND, "model_group_key": None}

    before = catalogue_browse.compute_facets(db_session, brand=None, **scope)
    assert before.state is CatalogueBrowseState.OK and before.coded == {}
    assert _catalogue_requests(s.fake) == [], "no brand chosen: nothing is asked"

    catalogue_browse.compute_facets(db_session, brand=BRAND, **scope)
    requests = _catalogue_requests(s.fake)
    assert len(requests) == len(FACET_FIELDS)
    distinct_fields = sorted(value for r in requests for key, value in r.query if key == "fields")
    assert distinct_fields == sorted(f"{catalogue_browse.CODED_FIELDS[f].provider_field} distinct" for f in FACET_FIELDS)
    assert all(("filter[brand]", BRAND) in r.query for r in requests), "every facet query is scoped to the brand"


def test_brands_and_model_groups_are_one_request_each(db_session, monkeypatch):
    s = _connected(
        db_session, monkeypatch,
        FakeAutoi(api_bodies={
            "/vehiclecatalog/brand/passenger": fix.brands_body(),
            f"/vehiclecatalog/modelgroup/passenger/{BRAND}": fix.model_groups_body(),
        }),
    )

    brands = catalogue_browse.list_brands(db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND)
    groups = catalogue_browse.list_model_groups(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=BRAND,
        mode=CatalogueBrowseMode.BUILD,
    )

    assert [b.name for b in brands.items] == ["Testmarke", "Zweitmarke"]
    assert [g.model_group_key for g in groups.items] == [fix.DEMO_MODEL_GROUP_KEY, "104912"]
    assert len(_catalogue_requests(s.fake)) == 2


def test_an_unscoped_variant_query_is_refused_before_anything_is_sent(db_session, monkeypatch):
    from app.core.errors import UnprocessableEntityError

    s = _connected(db_session, monkeypatch, FakeAutoi())
    with pytest.raises(UnprocessableEntityError):
        catalogue_browse.browse_variants(
            db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id, vehicle_kind=KIND, brand=None,
            model_group_key=None, filters=VariantFilters(), sort=[], page=1,
        )
    assert s.fake.api_requests() == []


def test_a_betrieb_without_a_connection_sends_nothing(db_session, monkeypatch):
    s = _connected(db_session, monkeypatch, FakeAutoi())
    result = catalogue_browse.browse_variants(
        db_session, tenant_id=uuid.uuid4(), actor_id=None, vehicle_kind=KIND, brand=BRAND, model_group_key=None,
        filters=VariantFilters(), sort=[], page=1,
    )
    assert result.state is CatalogueBrowseState.NOT_CONFIGURED and result.rows == []
    assert s.fake.api_requests() == []
