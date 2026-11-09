"""KAN-552 — the valuation's provider figure through the identAPI, on the
demo provider (the real adapter's operations are pinned over the fake
transport in `test_identapi_valuation_adapter.py`).

Exit criterion 2: granted → value stored and marked; not granted → 409
`not_entitled` (and the strict capability the dialog reads says no); outage →
422 `provider_unavailable`; a manual override keeps both figures; a
configuration without a FzKey cannot ask (422 `no_catalogue_variant`).
Exit criterion 3: opening a valuation makes no call.
Anto, 2026-11-09: the quote is fetched and stored before the valuation is
created (option A, ADR-066 holds); «Wert auto-i-dat» is the trade-in price,
purchase and sale beside it.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.base import VehicleValuationData
from app.integration.errors import ProviderNotEntitledError, ProviderTimeoutError
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import resilience
from app.valuation.models.valuation import ValuationProviderQuote
from app.vehicle.models.configuration import ConfigurationMatchMethod, ConfigurationMode, ConfigurationSource
from app.vehicle.schemas.configuration import ConfigurationCreate, ConfigurationOptionInput
from app.vehicle.services import configuration as configuration_service
from tests.catalogue_lookup_support import see_demo_variant
from tests.configuration_test_support import MANUAL_CODED, seed_manual_coded_lists

FIRST_REGISTRATION = dt.date(2020, 3, 2)
MILEAGE = 41000
VIN = "ZARFAMBN5L7552001"


@pytest.fixture(autouse=True)
def _fresh_breakers():
    yield
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


def _connected(db_session, *, bewertung: bool | None = True) -> uuid.UUID:
    provider = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == "auto_i_dat_mock"))
    if provider is None:
        provider = IntegrationProvider(
            provider_code="auto_i_dat_mock", category="vehicle_data", display_name="auto-i-dat (mock)",
            auth_type="none", required_secret_slots=[], capability_codes=["ident_basis", "bewertung"],
        )
        db_session.add(provider)
        db_session.commit()
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )
    if bewertung is not None:
        connection_service.record_probed_entitlement(
            db_session, connection_id=connection.id, capability_code="bewertung", granted=bewertung
        )
    db_session.commit()
    return tenant_id


def _provider_configuration(db_session, tenant_id: uuid.UUID) -> str:
    variant = see_demo_variant(db_session, "FZ100001")
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
            vin=VIN, first_registration_date=FIRST_REGISTRATION, mileage_km=MILEAGE,
        ),
    )
    db_session.commit()
    configuration_service.replace_options(
        db_session, configuration=config, actor_id=uuid.uuid4(),
        options=[
            ConfigurationOptionInput(description="Navigationssystem", option_code="912001", price=Decimal("1200.00")),
            ConfigurationOptionInput(description="Abgewählt", option_code="912002", price=Decimal(500), selected=False),
            ConfigurationOptionInput(description="Serie", option_code="912003", price=Decimal(0), is_included=True),
            ConfigurationOptionInput(description="Kein OptKey", option_code="HP1", price=Decimal(300)),
        ],
    )
    db_session.commit()
    return str(config.id)


def _manual_configuration(db_session, tenant_id: uuid.UUID) -> str:
    seed_manual_coded_lists(db_session)
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD, **MANUAL_CODED,
            match_method=ConfigurationMatchMethod.MANUAL, brand_display_name="Lada", model_group_name="Niva",
            vin=VIN, first_registration_date=FIRST_REGISTRATION, mileage_km=MILEAGE,
        ),
    )
    db_session.commit()
    return str(config.id)


def _calls(db_session, operation: str | None = None) -> int:
    query = select(func.count()).select_from(IntegrationCallLog)
    if operation is not None:
        query = query.where(IntegrationCallLog.capability == operation)
    return db_session.scalar(query)


def _quote(client, tenant_id: uuid.UUID, configuration_id: str, **fields):
    return client.post(
        "/v1/valuations/provider-quotes", json={"configurationId": configuration_id, **fields}, headers=_headers(tenant_id)
    )


def _valuation(client, tenant_id: uuid.UUID, configuration_id: str, **fields):
    return client.post(
        "/v1/valuations",
        json={"configurationId": configuration_id, "finalOffer": "15000.00", **fields},
        headers=_headers(tenant_id),
    )


# --- granted -------------------------------------------------------------------------


def test_a_granted_quote_is_one_valuation_and_one_table_request_and_the_valuation_takes_its_figure(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 201, response.text
    quote = response.json()
    assert quote["fzKey"] == "FZ100001" and quote["modelYear"] == 2020
    assert quote["firstRegistration"] == FIRST_REGISTRATION.isoformat() and quote["mileage"] == MILEAGE
    exchange = Decimal(quote["exchangePrice"])
    assert Decimal(quote["purchasePrice"]) < exchange < Decimal(quote["salePrice"])
    assert quote["residualTableState"] == "present" and len(quote["residualTable"]) == 30
    assert quote["screenMessages"][0]["fr"]  # provider text in the four languages
    # one request each, both under the quote's correlation id
    assert _calls(db_session, "vehiclevaluation") == 1 and _calls(db_session, "residualvalue_table") == 1
    refs = set(db_session.scalars(select(IntegrationCallLog.correlation_id)).all())
    assert refs == {uuid.UUID(quote["providerRequestRef"])}
    # only the selected, non-standard options with a six-digit OptKey are valued
    stored = db_session.get(ValuationProviderQuote, uuid.UUID(quote["id"]))
    assert stored.option_keys == [912001]

    created = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"])

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "auto_i_dat" and Decimal(body["providerValue"]) == exchange
    assert body["providerQuote"]["id"] == quote["id"] and len(body["providerQuote"]["residualTable"]) == 30


def test_a_manual_override_keeps_both_figures(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()

    created = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"], providerValue="99.00")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "manual" and body["providerValue"] == "99.00"
    assert body["providerQuote"]["exchangePrice"] == quote["exchangePrice"]


def test_a_quote_values_what_the_dialog_shows_and_a_valuation_must_state_the_same(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    quote = _quote(client, tenant_id, configuration_id, mileage=60000, firstRegistration="2021-01-04").json()
    assert quote["mileage"] == 60000 and quote["modelYear"] == 2021

    stale = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"])
    assert stale.status_code == 422, stale.text
    assert stale.json()["error"]["details"] == {
        "reason": "provider_quote_stale", "fields": ["vehicleFirstRegistration", "mileage"],
    }

    taken = _valuation(
        client, tenant_id, configuration_id, providerQuoteId=quote["id"], mileage=60000,
        vehicleFirstRegistration="2021-01-04",
    )
    assert taken.status_code == 201, taken.text
    assert taken.json()["source"] == "auto_i_dat"


def test_an_auto_i_dat_figure_without_a_quote_is_refused(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    response = _valuation(client, tenant_id, configuration_id, source="auto_i_dat", providerValue="20000.00")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_quote_required"}
    # a typed figure is the advisor's own: manual
    typed = _valuation(client, tenant_id, configuration_id, providerValue="20000.00")
    assert typed.status_code == 201 and typed.json()["source"] == "manual" and typed.json()["providerQuote"] is None


def test_another_betriebs_quote_is_404_and_another_configurations_is_refused(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()

    other_tenant = _connected(db_session)
    other_configuration = _provider_configuration(db_session, other_tenant)
    assert _valuation(client, other_tenant, other_configuration, providerQuoteId=quote["id"]).status_code == 404

    second = _provider_configuration(db_session, tenant_id)
    mismatch = _valuation(client, tenant_id, second, providerQuoteId=quote["id"])
    assert mismatch.status_code == 422 and mismatch.json()["error"]["details"] == {"reason": "provider_quote_mismatch"}


# --- not granted, refused, unavailable ------------------------------------------------


@pytest.mark.parametrize("bewertung", [None, False], ids=["never-probed", "probed-not-granted"])
def test_without_the_bewertung_option_nothing_is_sent_and_the_capability_says_no(client, db_session, bewertung):
    tenant_id = _connected(db_session, bewertung=bewertung)
    configuration_id = _provider_configuration(db_session, tenant_id)

    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"reason": "not_entitled"}
    assert _calls(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(ValuationProviderQuote)) == 0
    # the dialog's button reads the same strict answer
    capability = client.get("/v1/integrations/capabilities/valuation", headers=_headers(tenant_id))
    assert capability.json() == {"capabilityCode": "valuation", "granted": False}


def test_with_the_bewertung_option_the_capability_says_yes(client, db_session):
    tenant_id = _connected(db_session)
    capability = client.get("/v1/integrations/capabilities/valuation", headers=_headers(tenant_id))
    assert capability.json() == {"capabilityCode": "valuation", "granted": True}


def test_a_refusal_at_call_time_is_not_entitled_and_an_outage_is_unavailable(client, db_session, monkeypatch):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    def refused(self, **_):
        raise ProviderNotEntitledError("vehiclevaluation")

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_vehicle_valuation", refused)
    response = _quote(client, tenant_id, configuration_id)
    assert response.status_code == 409 and response.json()["error"]["details"] == {"reason": "not_entitled"}

    def slow(self, **_):
        raise ProviderTimeoutError("vehiclevaluation: timed out")

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_vehicle_valuation", slow)
    response = _quote(client, tenant_id, configuration_id)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "provider_unavailable"}
    assert db_session.scalar(select(func.count()).select_from(ValuationProviderQuote)) == 0


def test_the_providers_own_cannot_value_is_said_with_its_message(client, db_session, monkeypatch):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    def too_old(self, **_):
        return VehicleValuationData(
            state=11, state_message="Fahrzeugalter überschritten", manually=False, exchange_price=None,
            purchase_price=None, sale_price=None, vehicle_exchange_price=None, options_exchange_price=None,
            kilometre_correction_exchange=None, kilometre_expected=None, exchange_range_from=None,
            exchange_range_to=None, screen_messages=[], print_messages=[],
        )

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_vehicle_valuation", too_old)
    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {
        "reason": "provider_cannot_value", "stateCode": 11, "providerMessage": "Fahrzeugalter überschritten",
    }
    assert _calls(db_session, "residualvalue_table") == 0, "no table is asked for a car the provider cannot value"


def test_a_failed_residual_table_keeps_the_valuation_and_says_why(client, db_session, monkeypatch):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)

    def slow(self, **_):
        raise ProviderTimeoutError("residualvalue_table: timed out")

    monkeypatch.setattr(MockAutoIDatAdapter, "fetch_residual_table", slow)
    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 201, response.text
    assert response.json()["residualTableState"] == "unavailable" and response.json()["residualTable"] == []


def test_a_configuration_without_a_fzkey_cannot_ask(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _manual_configuration(db_session, tenant_id)

    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "no_catalogue_variant"}
    assert _calls(db_session) == 0


def test_without_a_connection_the_quote_names_it(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    db_session.query(IntegrationConnection).update({"enabled": False})
    db_session.commit()

    response = _quote(client, tenant_id, configuration_id)

    assert response.status_code == 409 and response.json()["error"]["details"] == {"reason": "no_provider_connection"}


def test_another_betriebs_configuration_is_404(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    other = _connected(db_session)

    assert _quote(client, other, configuration_id).status_code == 404
    assert _calls(db_session) == 0


# --- exit criterion 3 ---------------------------------------------------------------------


def test_opening_a_valuation_makes_no_provider_call(client, db_session):
    tenant_id = _connected(db_session)
    configuration_id = _provider_configuration(db_session, tenant_id)
    quote = _quote(client, tenant_id, configuration_id).json()
    created = _valuation(client, tenant_id, configuration_id, providerQuoteId=quote["id"]).json()
    calls = _calls(db_session)

    detail = client.get(f"/v1/valuations/{created['id']}", headers=_headers(tenant_id))
    listing = client.get("/v1/valuations", headers=_headers(tenant_id))

    assert detail.status_code == 200 and listing.status_code == 200
    assert detail.json()["providerQuote"]["exchangePrice"] == quote["exchangePrice"]
    assert _calls(db_session) == calls, "opening a valuation never asks the provider"
