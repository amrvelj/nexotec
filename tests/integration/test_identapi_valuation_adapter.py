"""KAN-552 — the identAPI adapter's valuation operations through the real
registered adapter over the fake authorization server and identAPI: one
request each under its own operation name, the options by OptKey, the
messages in four languages, the provider's own «cannot value» read as a
state, a 403 as `ProviderNotEntitledError`. The residual table is one
request — never a loop over durations or km bands.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.integration.adapters.auto_i_dat_ident import OP_RESIDUALVALUE_TABLE, OP_VEHICLEVALUATION
from app.integration.errors import ProviderNotEntitledError
from app.integration.models.call_log import IntegrationCallLog
from app.integration.services import resilience
from app.integration.services.gateway import call_capability
from tests import identapi_fixtures as fix
from tests import identapi_support as fx
from tests.identapi_support import FakeAutoi

FIRST = dt.date(2022, 5, 17)
VALUATION_PATH = f"/vehiclevaluation/01911001/2022/{FIRST.isoformat()}/52000"
TABLE_PATH = "/residualvalue/01911001/2022"


@pytest.fixture(autouse=True)
def _fresh_adapter_state():
    yield
    fx.uninstall()
    for connection_id in list(resilience._CIRCUITS):
        resilience.reset_circuit(connection_id)


def _ask(db_session, s, fn):
    with call_capability(db_session, connection=s.connection, capability="valuation", actor_id=s.user.id) as adapter:
        return fn(adapter)


def _rows(db_session, operation: str) -> int:
    return db_session.query(IntegrationCallLog).filter_by(capability=operation).count()


def _valuate(adapter):
    return adapter.fetch_vehicle_valuation(
        fz_key="01911001", model_year=2022, first_registration=FIRST, mileage_km=52000, option_keys=[912001, 912002]
    )


def test_a_valuation_is_one_request_with_the_options_and_four_languages(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_bodies={VALUATION_PATH: fix.valuation_body()}))

    answer = _ask(db_session, s, _valuate)

    (request,) = s.fake.api_requests_to(VALUATION_PATH)
    assert [v for k, v in request.query if k == "lang"] == ["de", "fr", "it", "en"]
    assert ("option[0].key", "912001") in request.query and ("option[1].key", "912002") in request.query
    assert _rows(db_session, OP_VEHICLEVALUATION) == 1
    assert answer.state == 0 and answer.manually is False
    # each price is the vehicle's plus its options'
    assert (answer.exchange_price, answer.purchase_price, answer.sale_price) == (Decimal(19000), Decimal(17600), Decimal(22700))
    assert answer.kilometre_correction_exchange == Decimal(-350) and answer.kilometre_expected == 45000
    (message,) = answer.screen_messages
    assert (message.de, message.fr, message.it, message.en) == ("Testhinweis", "Note de test", "Nota di prova", "Test note")


def test_the_providers_own_refusal_to_value_is_a_state_not_a_price(db_session, monkeypatch):
    body = fix.valuation_body(info={"state": "VehicleAgeExceeded", "stateCode": 11, "stateMsg": "Fahrzeug zu alt"})
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_bodies={VALUATION_PATH: body}))

    answer = _ask(db_session, s, _valuate)

    assert answer.state == 11 and answer.state_message == "Fahrzeug zu alt"


def test_an_unknown_car_and_an_unreadable_state_are_never_a_valuation(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={VALUATION_PATH: 404}))
    assert _ask(db_session, s, _valuate).state == -1

    s.fake.api_status.clear()
    s.fake.api_bodies[VALUATION_PATH] = fix.valuation_body(info={"state": None, "stateCode": None})
    assert _ask(db_session, s, _valuate).state == -1


def test_a_valuation_outside_the_licence_is_provider_not_entitled(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_status={VALUATION_PATH: 403}))

    with pytest.raises(ProviderNotEntitledError):
        _ask(db_session, s, _valuate)


def test_the_residual_table_is_one_request_for_the_used_car(db_session, monkeypatch):
    s = fx.connected(db_session, monkeypatch, fake=FakeAutoi(api_bodies={TABLE_PATH: fix.residual_table_body()}))

    table = _ask(
        db_session, s,
        lambda a: a.fetch_residual_table(
            fz_key="01911001", model_year=2022, first_registration=FIRST, mileage_km=52000, option_keys=[]
        ),
    )

    (request,) = s.fake.api_requests()
    assert request.path == TABLE_PATH
    assert ("firstAdmission", FIRST.isoformat()) in request.query and ("kilometreState", "52000") in request.query
    assert _rows(db_session, OP_RESIDUALVALUE_TABLE) == 1
    assert table.state == 0 and len(table.rows) == 6
    assert {(r.duration_months, r.km_per_year) for r in table.rows} == {(m, k) for m in (12, 24, 36) for k in (10000, 20000)}
