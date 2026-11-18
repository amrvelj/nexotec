"""KAN-552 exit criterion 2, through the real `auto_i_dat` adapter: the
quote endpoint on an identAPI connection over the fake authorization server
and identAPI (`tests/identapi_support.py`) — the FzKey read under
`auto_i_dat`, the enrolled user, `_request`'s retry and judgement — for a
granted answer, the provider's 403, an outage (timeout and 5xx, each retried
once), the provider's own «cannot value» and its «offline» state.
"""

import datetime as dt
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.integration.adapters import identapi_parse as parse
from app.integration.models.call_log import IntegrationCallLog
from app.integration.services import connections as connection_service
from app.integration.services import resilience
from app.valuation.models.valuation import ValuationProviderQuote
from app.vehicle.models.configuration import ConfigurationMatchMethod, ConfigurationMode, ConfigurationSource
from app.vehicle.schemas.configuration import ConfigurationCreate
from app.vehicle.services import catalogue_sync
from app.vehicle.services import configuration as configuration_service
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi

FIRST = dt.date(2022, 5, 17)
MILEAGE = 52000
VALUATION_PATH = f"/vehiclevaluation/{fix.DEMO_FZ_KEY}/2022/{FIRST.isoformat()}/{MILEAGE}"
TABLE_PATH = f"/residualvalue/{fix.DEMO_FZ_KEY}/2022"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _setup(db_session, monkeypatch, fake: FakeAutoi):
    s = fx.connected(db_session, monkeypatch, fake=fake)
    connection_service.record_probed_entitlement(
        db_session, connection_id=s.connection.id, capability_code="bewertung", granted=True
    )
    master = parse.parse_variant(fix.catalog_variant())
    assert master is not None
    variant = catalogue_sync.upsert_model_variant(db_session, provider_code="auto_i_dat", master=master)
    db_session.commit()
    config = configuration_service.create_configuration(
        db_session, tenant_id=s.connection.tenant_id, actor_id=s.user.id,
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
            vin="WVWZZZTESTV552001", first_registration_date=FIRST, mileage_km=MILEAGE,
        ),
    )
    db_session.commit()
    token = create_access_token(
        user_id=s.user.id, tenant_id=s.connection.tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return s, str(config.id), token


def _post(client, token: str, path: str, body: dict):
    return client.post(
        path, json=body, headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    )


def _rows(db_session, operation: str) -> int:
    return db_session.scalar(
        select(func.count()).select_from(IntegrationCallLog).where(IntegrationCallLog.capability == operation)
    )


def _quotes(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ValuationProviderQuote))


def test_granted_the_quote_is_stored_and_the_valuation_marked(client, db_session, monkeypatch):
    fake = FakeAutoi(api_bodies={VALUATION_PATH: fix.valuation_body(), TABLE_PATH: fix.residual_table_body()})
    s, configuration_id, token = _setup(db_session, monkeypatch, fake)

    response = _post(client, token, "/v1/valuations/provider-quotes", {"configurationId": configuration_id})

    assert response.status_code == 201, response.text
    quote = response.json()
    assert Decimal(quote["exchangePrice"]) == Decimal(19000) and quote["residualTableState"] == "present"
    assert len(s.fake.api_requests_to(VALUATION_PATH)) == 1 and len(s.fake.api_requests_to(TABLE_PATH)) == 1
    assert _rows(db_session, "vehiclevaluation") == 1 and _rows(db_session, "residualvalue_table") == 1
    # the token was requested for the acting user (auto-i's User Enrollment Grant)
    assert s.fake.token_requests() and s.fake.token_requests()[-1].form["email"] == s.user.email

    created = _post(
        client, token, "/v1/valuations",
        {"configurationId": configuration_id, "providerQuoteId": quote["id"], "finalOffer": "18000.00"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "auto_i_dat" and Decimal(created.json()["providerValue"]) == Decimal(19000)


def test_the_providers_403_is_not_entitled(client, db_session, monkeypatch):
    s, configuration_id, token = _setup(db_session, monkeypatch, FakeAutoi(api_status={VALUATION_PATH: 403}))

    response = _post(client, token, "/v1/valuations/provider-quotes", {"configurationId": configuration_id})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "not_entitled"}
    assert _quotes(db_session) == 0 and s.fake.api_requests_to(TABLE_PATH) == []


@pytest.mark.parametrize("outage", ["timeout", "http_503"])
def test_an_outage_is_unavailable_after_one_retry(client, db_session, monkeypatch, outage):
    fake = FakeAutoi()
    if outage == "timeout":
        fake.raise_on_api = httpx.ReadTimeout("slow")
    else:
        fake.api_status[VALUATION_PATH] = 503
    _s, configuration_id, token = _setup(db_session, monkeypatch, fake)

    response = _post(client, token, "/v1/valuations/provider-quotes", {"configurationId": configuration_id})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_unavailable"}
    assert _rows(db_session, "vehiclevaluation") == 2, "a timeout or 5xx is retried once, each attempt its own row"
    assert _quotes(db_session) == 0


def test_the_providers_cannot_value_and_offline_states(client, db_session, monkeypatch):
    too_old = fix.valuation_body(info={"state": "VehicleAgeExceeded", "stateCode": 11, "stateMsg": "Fahrzeug zu alt"})
    s, configuration_id, token = _setup(db_session, monkeypatch, FakeAutoi(api_bodies={VALUATION_PATH: too_old}))

    response = _post(client, token, "/v1/valuations/provider-quotes", {"configurationId": configuration_id})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {
        "reason": "provider_cannot_value", "stateCode": 11, "providerMessage": "Fahrzeug zu alt",
    }

    s.fake.api_bodies[VALUATION_PATH] = fix.valuation_body(info={"state": "Offline", "stateCode": 1, "stateMsg": "Wartung"})
    response = _post(client, token, "/v1/valuations/provider-quotes", {"configurationId": configuration_id})
    assert response.status_code == 422 and response.json()["error"]["details"] == {"reason": "provider_unavailable"}
    assert s.fake.api_requests_to(TABLE_PATH) == [] and _quotes(db_session) == 0
