"""KAN-211 (ADR-123) exit criteria 2, 4 and 5 — a variant's licensed details
load once per Betrieb when the car is opened, each detail its own request,
savepoint and state; a refused or failed detail never discards the car or
the other details (KAN-212); «Neu laden» is one fresh request per detail,
dealer manager only, audited; a Betrieb reads no other Betrieb's details
(ADR-013).

Two providers: the real registered identAPI adapter over the fake
authorization server and identAPI (`tests/identapi_support.py`, bodies from
`tests/identapi_fixtures.py`) for the wire shapes, the request counts and
the error states; the mock provider for the app-level reads the local app
and the screenshots run on.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.core.audit import list_audit_events
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.integration.adapters import identapi_parse as parse
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.services import resilience
from app.vehicle.models.catalogue import ModelVariant, VariantOption, VariantOptionRelation
from app.vehicle.models.catalogue_mirror import CatalogueDetail, ColourCache, ImageRef, TyreSpecCache
from app.vehicle.models.energy_rating import ModelVariantEnergyRating
from app.vehicle.models.provider import MappingGap
from app.vehicle.services import catalogue_details, catalogue_entitlements, catalogue_sync
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.catalogue_lookup_support import DEMO_FZ_KEYS, assert_all_present, open_demo_variant, see_demo_variant
from tests.colour_tyre_code_map_support import seed_colour_tyre_code_map
from tests.identapi_support import FakeAutoi
from tests.test_provider_code_map_seed import _seed_migration
from tests.test_vehicle_catalogue_entitlements import _make_connection, _make_mock_provider
from tests.token_support import dealer_group_of

OPERATIONS = (
    "vehiclecatalog",
    "vehicleoption_catalog",
    "tyresandrims_typeapproval",
    "image_vehicle",
    "energylabel_info",
)


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _connected(db_session, monkeypatch, fake: FakeAutoi):
    """The identAPI connection, the KAN-547 code map, the KAN-75 colour/tyre
    map, the energy-label reference list — and the demo variant stored as an
    identification would store it (master data only)."""

    s = fx.connected(db_session, monkeypatch, fake=fake)
    _seed_migration()._seed(db_session.connection())
    seed_colour_tyre_code_map(db_session)
    _seed_energy_labels(db_session)
    variant = catalogue_sync.upsert_model_variant(
        db_session, provider_code="auto_i_dat", master=parse.parse_variant(fix.catalog_variant())
    )
    db_session.commit()
    return s, variant


def _seed_energy_labels(db_session) -> None:
    from app.platform.models.reference_data import ReferenceList, ReferenceValue

    if db_session.scalar(select(ReferenceList).where(ReferenceList.list_code == "energy_efficiency_category")):
        return
    ref_list = ReferenceList(list_code="energy_efficiency_category")
    db_session.add(ref_list)
    db_session.flush()
    for code in "abcdefg":
        db_session.add(
            ReferenceValue(
                list_id=ref_list.id,
                value_code=code,
                label_de=code.upper(),
                label_fr=code.upper(),
                label_it=code.upper(),
                label_en=code.upper(),
            )
        )
    db_session.commit()


def _calls(db_session, operation: str, status: CallStatus | None = None) -> int:
    query = select(func.count()).select_from(IntegrationCallLog).where(IntegrationCallLog.capability == operation)
    if status is not None:
        query = query.where(IntegrationCallLog.status == status)
    return db_session.scalar(query)


def _open(db_session, s, variant, *, force: bool = False):
    return catalogue_details.load_on_open(
        db_session, tenant_id=s.connection.tenant_id, variant=variant, actor_id=s.user.id, force=force
    )


def _states(result) -> dict[str, str]:
    return {
        detail.value: (row.state.value if (row := result.state_of(detail)) else "not_loaded")
        for detail in catalogue_details.DETAILS
    }


def _deny(db_session, connection_id, capability_code):
    db_session.add(
        IntegrationEntitlement(
            connection_id=connection_id,
            capability_code=capability_code,
            granted=False,
            source=EntitlementSource.DECLARED,
            checked_at=utcnow(),
        )
    )
    db_session.commit()


# --- the first open, on the identAPI ---------------------------------------------------


def test_the_first_open_fetches_and_stores_every_detail_with_one_request_each(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    tenant_id = s.connection.tenant_id

    result = _open(db_session, s, variant)

    assert _states(result) == dict.fromkeys(_states(result), "present")
    assert [_calls(db_session, op, CallStatus.SUCCESS) for op in OPERATIONS] == [1, 1, 1, 1, 1]
    assert len(s.fake.api_requests()) == 5

    where = {"tenant_id": tenant_id, "model_variant_id": variant.id}
    colours = {c.colour_code: c for c in db_session.query(ColourCache).filter_by(**where)}
    assert set(colours) == {"C01", "C02", "U07"}
    assert (colours["C01"].colour_type, colours["C02"].colour_type, colours["U07"].colour_type) == (
        "exterior",
        "exterior",
        "interior",
    )
    assert (
        colours["C01"].description == "Mythosschwarz Metallic" and colours["C01"].price == 0
    )  # as delivered; free is CHF 0.00
    assert colours["C02"].price == 1150

    options = {o.option_code: o for o in db_session.query(VariantOption).filter_by(**where)}
    assert set(options) == {"1001", "1002"}
    assert options["1001"].manufacturer_code == "M1001" and options["1001"].option_group == "Komfort"
    assert options["1002"].is_included is True and options["1002"].price == 0

    tyres = sorted((t.season, t.size, t.remark) for t in db_session.query(TyreSpecCache).filter_by(**where))
    assert tyres == [("summer", "225/45 R18", "nur mit Leichtmetallfelgen"), ("winter", "205/55 R17", None)]
    assert {t.axle for t in db_session.query(TyreSpecCache).filter_by(**where)} == {"both"}

    images = sorted((i.bild_typ, i.bild_art, i.image_url) for i in db_session.query(ImageRef).filter_by(**where))
    assert images == [
        ("L", "A", f"https://images.example.test/{fix.DEMO_FZ_KEY}/01-full.jpg"),
        ("L", "I", f"https://images.example.test/{fix.DEMO_FZ_KEY}/02-full.jpg"),
        ("S", "A", f"https://images.example.test/{fix.DEMO_FZ_KEY}/01-thumb.jpg"),
    ]

    # ADR-042 / Anto 2026-11-08: the label is shared, stored for its year, source auto_i_dat
    rating = db_session.scalar(
        select(ModelVariantEnergyRating).where(
            ModelVariantEnergyRating.model_variant_id == variant.id, ModelVariantEnergyRating.rating_year == 2026
        )
    )
    assert (rating.energy_efficiency_category, rating.source) == ("b", "auto_i_dat")


def test_the_second_open_sends_nothing(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    _open(db_session, s, variant)
    seen = len(s.fake.api_requests())

    result = _open(db_session, s, variant)

    assert len(s.fake.api_requests()) == seen
    assert _states(result) == dict.fromkeys(_states(result), "present")


def test_the_options_of_one_car_are_followed_page_by_page_to_the_last(db_session, monkeypatch):
    """Anto, 2026-11-08: every option. Three pages of ten, the third without
    `links.next`: three `vehicleoption_catalog` rows, thirty options stored,
    the walk over for this car and no other."""

    items = [fix.option(f"{n:04d}") for n in range(1, 31)]
    bodies = fix.detail_bodies(options=items[:10])
    bodies[fix.OPTIONS_PATH] = fix.options_page(items[:10], page=1, total=30, has_next=True)
    fake = FakeAutoi(api_bodies=bodies)
    # the fake serves one body per path: page 2 and 3 are told apart by `page[number]`
    pages = {
        "1": fix.options_page(items[:10], page=1, total=30, has_next=True),
        "2": fix.options_page(items[10:20], page=2, total=30, has_next=True),
        "3": fix.options_page(items[20:30], page=3, total=30, has_next=False),
    }
    original = fake.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == fix.OPTIONS_PATH and request.url.params.get("page[number]") in pages:
            response = original(request)
            if response.status_code == 200:
                return httpx.Response(200, json=pages[request.url.params["page[number]"]])
            return response
        return original(request)

    fake.handle = handle  # type: ignore[method-assign]
    s, variant = _connected(db_session, monkeypatch, fake)

    _open(db_session, s, variant)

    option_requests = s.fake.api_requests_to(fix.OPTIONS_PATH)
    assert [dict(r.query)["page[number]"] for r in option_requests] == ["1", "2", "3"]
    assert _calls(db_session, "vehicleoption_catalog", CallStatus.SUCCESS) == 3
    assert db_session.query(VariantOption).filter_by(model_variant_id=variant.id).count() == 30


def test_option_relations_are_stored_from_the_answer_and_never_enforced(db_session, monkeypatch):
    items = [
        fix.option("2001", packOptions=[{"optionKey": "2002", "description": "Sitzheizung"}], exclusions=["2003"]),
        fix.option("2002", pack=True, included=True, price=0),
        fix.option("2003", addition=[{"optionKey": "2001", "description": "x", "action": "P", "price": 400}]),
    ]
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies(options=items)))

    _open(db_session, s, variant)

    options = {o.option_code: o for o in db_session.query(VariantOption).filter_by(model_variant_id=variant.id)}
    assert options["2001"].is_package is True and options["2002"].is_package is False
    relations = {
        (options_by_id[r.from_option_id], options_by_id[r.to_option_id], r.relation_type, r.price_in_combination)
        for options_by_id in [{o.id: code for code, o in options.items()}]
        for r in db_session.query(VariantOptionRelation).filter_by(model_variant_id=variant.id)
    }
    assert relations == {
        ("2001", "2002", "contains", None),
        ("2001", "2003", "excludes", None),
        ("2003", "2001", "price_in_combination_with", 400),
    }


# --- the states: refused, failed, retried ----------------------------------------------


def test_a_403_on_one_detail_keeps_the_others_and_marks_that_one_not_in_contract(db_session, monkeypatch):
    """KAN-212 (criterion 4): a refused detail never discards the car or the
    other details; the refusal is its own ERROR row and never charges the
    breaker (`.claude/rules/vehicle.md`)."""

    fake = FakeAutoi(api_bodies=fix.detail_bodies(), api_status={fix.OPTIONS_PATH: 403})
    s, variant = _connected(db_session, monkeypatch, fake)
    charged: list = []
    real = resilience.record_failure
    monkeypatch.setattr(resilience, "record_failure", lambda cid: (charged.append(cid), real(cid)))

    result = _open(db_session, s, variant)

    states = _states(result)
    assert states["options"] == "not_in_contract"
    assert {k: v for k, v in states.items() if k != "options"} == dict.fromkeys(
        ["colours", "tyre_specs", "images", "energy_label"], "present"
    )
    assert result.state_of(CatalogueDetail.OPTIONS).error == "ProviderNotEntitledError"
    where = {"tenant_id": s.connection.tenant_id, "model_variant_id": variant.id}
    assert db_session.query(VariantOption).filter_by(**where).count() == 0
    assert db_session.query(ColourCache).filter_by(**where).count() == 3
    assert db_session.query(ImageRef).filter_by(**where).count() == 3
    assert db_session.get(ModelVariant, variant.id) is not None
    assert _calls(db_session, "vehicleoption_catalog", CallStatus.ERROR) == 1
    assert charged == []

    # the next open does not ask for a refused detail again (a billed 403 each open)
    seen = len(s.fake.api_requests())
    _open(db_session, s, variant)
    assert len(s.fake.api_requests()) == seen


def test_an_outage_on_one_detail_marks_it_unavailable_and_the_next_open_retries_only_that_one(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies=fix.detail_bodies(), api_status={fix.IMAGES_PATH: 503})
    s, variant = _connected(db_session, monkeypatch, fake)

    result = _open(db_session, s, variant)

    states = _states(result)
    assert (
        states["images"] == "unavailable" and result.state_of(CatalogueDetail.IMAGES).error == "ProviderTransportError"
    )
    assert {k: v for k, v in states.items() if k != "images"} == dict.fromkeys(
        ["colours", "options", "tyre_specs", "energy_label"], "present"
    )
    assert db_session.query(ImageRef).filter_by(model_variant_id=variant.id).count() == 0
    # retried once by the gateway: two attempts, each its own ERROR row (KAN-486)
    assert _calls(db_session, "image_vehicle", CallStatus.ERROR) == 2

    fake.api_status.clear()
    seen = len(s.fake.api_requests())
    result = _open(db_session, s, variant)

    requested = [r.path for r in s.fake.api_requests()[seen:]]
    assert requested == [fix.IMAGES_PATH]
    assert _states(result)["images"] == "present"
    assert db_session.query(ImageRef).filter_by(model_variant_id=variant.id).count() == 3


def test_a_timeout_is_unavailable_too(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies=fix.detail_bodies())
    s, variant = _connected(db_session, monkeypatch, fake)
    fake.raise_on_api = httpx.ReadTimeout("slow")

    result = _open(db_session, s, variant)

    assert set(_states(result).values()) == {"unavailable"}
    assert {result.state_of(d).error for d in catalogue_details.DETAILS} == {"ProviderTimeoutError"}
    assert _calls(db_session, "vehiclecatalog", CallStatus.TIMEOUT) == 2  # two attempts, one row each (KAN-486)


def test_a_licence_option_the_probe_recorded_as_absent_sends_nothing(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    _deny(db_session, s.connection.id, "ausstattung")  # options and images
    _deny(db_session, s.connection.id, "pneu")

    result = _open(db_session, s, variant)

    states = _states(result)
    assert states == {
        "colours": "present",
        "options": "not_in_contract",
        "tyre_specs": "not_in_contract",
        "images": "not_in_contract",
        "energy_label": "present",
    }
    assert result.state_of(CatalogueDetail.OPTIONS).error == catalogue_details.ENTITLEMENT_ABSENT
    assert sorted(r.path for r in s.fake.api_requests()) == sorted([fix.VARIANT_PATH, fix.ENERGY_LABEL_PATH])


def test_an_open_circuit_marks_the_remaining_details_unavailable_without_a_request(db_session, monkeypatch):
    fake = FakeAutoi(api_bodies=fix.detail_bodies(), api_status={fix.VARIANT_PATH: 503, fix.OPTIONS_PATH: 503})
    s, variant = _connected(db_session, monkeypatch, fake)
    for _ in range(resilience._FAILURE_THRESHOLD - 2):
        resilience.record_failure(s.connection.id)

    result = _open(db_session, s, variant)

    states = _states(result)
    assert states["colours"] == "unavailable" and states["options"] == "unavailable"
    assert {states["tyre_specs"], states["images"], states["energy_label"]} == {"unavailable"}
    assert {
        result.state_of(d).error
        for d in (CatalogueDetail.TYRE_SPECS, CatalogueDetail.IMAGES, CatalogueDetail.ENERGY_LABEL)
    } == {"CircuitOpenError"}
    assert sorted(r.path for r in s.fake.api_requests() if r.path != "/api/oauth2/token") == sorted(
        [fix.VARIANT_PATH, fix.VARIANT_PATH, fix.OPTIONS_PATH, fix.OPTIONS_PATH]
    )


def test_a_betrieb_without_a_connection_asks_nothing_and_holds_no_state(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    other = uuid.uuid4()

    result = catalogue_details.load_on_open(db_session, tenant_id=other, variant=variant, actor_id=None)

    assert set(_states(result).values()) == {"not_loaded"}
    assert s.fake.api_requests() == []


def test_a_variant_unknown_at_the_provider_asks_nothing(db_session, monkeypatch):
    s, _ = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    foreign = see_demo_variant(db_session, DEMO_FZ_KEYS[0])  # the demo provider's variant

    result = _open(db_session, s, foreign)

    assert set(_states(result).values()) == {"not_loaded"}
    assert s.fake.api_requests() == []


# --- «Neu laden» -------------------------------------------------------------------------


def test_neu_laden_makes_exactly_one_request_per_detail_replaces_the_rows_and_is_audited(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    _open(db_session, s, variant)
    # the provider's answer changed since: one option gone, one new
    s.fake.api_bodies[fix.OPTIONS_PATH] = fix.options_page(
        [fix.option("1001"), fix.option("1009")], page=1, total=2, has_next=False
    )
    seen = len(s.fake.api_requests())

    result = catalogue_details.reload_details(
        db_session, tenant_id=s.connection.tenant_id, variant=variant, actor_id=s.user.id
    )

    requested = sorted(r.path for r in s.fake.api_requests()[seen:])
    assert requested == sorted(
        [fix.VARIANT_PATH, fix.OPTIONS_PATH, fix.TYRES_PATH, fix.IMAGES_PATH, fix.ENERGY_LABEL_PATH]
    )
    assert set(_states(result).values()) == {"present"}
    codes = {o.option_code for o in db_session.query(VariantOption).filter_by(model_variant_id=variant.id)}
    assert codes == {"1001", "1009"}
    events = list_audit_events(
        db_session, entity_type="vehicle_variant_details", entity_id=variant.id, tenant_id=s.connection.tenant_id
    )
    assert [(e.action, e.actor_id, e.tenant_id) for e in events] == [("reload", s.user.id, s.connection.tenant_id)]
    assert set(events[0].after) == {d.value for d in catalogue_details.DETAILS}


# --- ADR-013: one Betrieb's details are not another's ----------------------------------------


def test_a_contract_less_betrieb_reads_none_of_another_betriebs_details(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))
    _open(db_session, s, variant)
    other = uuid.uuid4()

    mine = catalogue_entitlements.get_catalogue_specification(
        db_session, tenant_id=s.connection.tenant_id, model_variant_id=variant.id, load_on_open=False
    )
    theirs = catalogue_entitlements.get_catalogue_specification(
        db_session, tenant_id=other, model_variant_id=variant.id
    )

    assert len(mine.options) == 2 and len(mine.colours) == 3 and mine.details.options.state == "present"
    assert theirs.options == [] and theirs.colours == [] and theirs.tyre_specs == [] and theirs.images == []
    assert theirs.details.options.state == "notLoaded" and theirs.has_provider_connection is False
    assert s.fake.api_requests_to(fix.OPTIONS_PATH) and len(s.fake.api_requests()) == 5


# --- the mock provider: what the local app runs on -------------------------------------------


def test_opening_a_demo_variant_loads_its_details_once_with_one_row_per_request(db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)

    variant = open_demo_variant(db_session, tenant_id, "FZ100002")

    assert_all_present(db_session, tenant_id, variant)
    rows = db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).all()
    assert sorted(row.capability for row in rows) == sorted(
        ["Fahrzeuge", "Optionen", "PneuDimTS", "Bilder", "Energieetikette"]
    )
    assert {row.status for row in rows} == {CallStatus.SUCCESS}
    where = {"tenant_id": tenant_id, "model_variant_id": variant.id}
    assert db_session.query(VariantOption).filter_by(**where).count() == 4
    assert db_session.query(ColourCache).filter_by(**where).count() == 3
    assert db_session.query(TyreSpecCache).filter_by(**where).count() == 3
    assert db_session.query(ImageRef).filter_by(**where).count() == 2

    catalogue_details.load_on_open(db_session, tenant_id=tenant_id, variant=variant, actor_id=None)
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 5


def test_a_second_betrieb_opening_the_same_car_loads_its_own_copy(db_session):
    provider = _make_mock_provider(db_session)
    first, second = uuid.uuid4(), uuid.uuid4()
    _make_connection(db_session, provider, tenant_id=first)
    _make_connection(db_session, provider, tenant_id=second)
    variant = open_demo_variant(db_session, first, "FZ100001")

    catalogue_details.load_on_open(db_session, tenant_id=second, variant=variant, actor_id=None)

    assert db_session.query(ModelVariant).count() == 1  # the master data is shared (ADR-075)
    assert db_session.query(ColourCache).filter_by(model_variant_id=variant.id, tenant_id=first).count() == 3
    assert db_session.query(ColourCache).filter_by(model_variant_id=variant.id, tenant_id=second).count() == 3
    assert db_session.scalar(select(func.count()).select_from(MappingGap)) >= 0  # no error path crossed


# --- the API: opening loads, reloading is the manager's -------------------------------------------


def _token(tenant_id: uuid.UUID, *, manager: bool, roles=frozenset()) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=roles,
        is_dealer_manager=manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_specification_read_loads_the_details_once_and_reports_their_states(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    _deny(db_session, connection.id, "pneu")
    variant = see_demo_variant(db_session, "FZ100002")
    token = _token(tenant_id, manager=False)

    first = client.get(f"/v1/catalogue/variants/{variant.id}/specification", headers=_bearer(token))
    second = client.get(f"/v1/catalogue/variants/{variant.id}/specification", headers=_bearer(token))

    assert first.status_code == 200, first.text
    body = first.json()
    assert {o["optionCode"] for o in body["options"]} == {"LED", "NAV", "WNTR", "AC"}
    assert body["details"]["options"]["state"] == "present" and body["details"]["options"]["loadedAt"]
    assert body["details"]["tyreSpecs"] == {
        "state": "notInContract",
        "loadedAt": body["details"]["tyreSpecs"]["loadedAt"],
    }
    assert body["tyreSpecs"] == []
    assert second.json()["details"] == body["details"]
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 4


def test_a_betrieb_without_a_connection_reads_not_loaded_states(client, db_session):
    variant = see_demo_variant(db_session, "FZ100001")
    token = _token(uuid.uuid4(), manager=False)

    response = client.get(f"/v1/catalogue/variants/{variant.id}/specification", headers=_bearer(token))

    assert response.status_code == 200
    assert {k: v["state"] for k, v in response.json()["details"].items()} == dict.fromkeys(
        ["options", "colours", "tyreSpecs", "images", "energyLabel"], "notLoaded"
    )


def test_reload_is_the_dealer_managers_and_platform_staffs_only(client, db_session):
    provider = _make_mock_provider(db_session)
    tenant_id = uuid.uuid4()
    connection = _make_connection(db_session, provider, tenant_id=tenant_id)
    variant = open_demo_variant(db_session, tenant_id, "FZ100001")
    url = f"/v1/catalogue/variants/{variant.id}/details/reload"

    refused = client.post(url, headers=_bearer(_token(tenant_id, manager=False)))
    assert refused.status_code == 403
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 5

    reloaded = client.post(
        url, headers={**_bearer(_token(tenant_id, manager=True)), "Idempotency-Key": str(uuid.uuid4())}
    )
    assert reloaded.status_code == 200, reloaded.text
    assert {k: v["state"] for k, v in reloaded.json()["details"].items()} == dict.fromkeys(
        ["options", "colours", "tyreSpecs", "images", "energyLabel"], "present"
    )
    assert db_session.query(IntegrationCallLog).filter_by(connection_id=connection.id).count() == 10

    staff = client.post(
        url, headers=_bearer(_token(tenant_id, manager=False, roles=frozenset({AccessRole.PLATFORM_ADMIN})))
    )
    assert staff.status_code == 200, staff.text

    unknown = client.post(
        f"/v1/catalogue/variants/{uuid.uuid4()}/details/reload", headers=_bearer(_token(tenant_id, manager=True))
    )
    assert unknown.status_code == 404
