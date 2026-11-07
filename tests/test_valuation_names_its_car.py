"""KAN-574 (Anto, 2026-11-07, option A): a valuation names its car.

`POST /v1/valuations` needs a VIN (or, for a car first registered before
1981, a chassis number — KAN-353), a make and a model: each defaulted from
the configuration when `configurationId` is sent (a field sent wins), typed
otherwise. A missing one
is 422 `valuation_names_no_car` with the missing `fields`, and nothing is
stored. Before, a valuation could be saved with a price alone, and attached
as a trade-in it lowered a deal's price for a car nobody could take in.
Over HTTP, on Postgres.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.valuation.models.valuation import Valuation
from tests.test_inventory_purchase_configuration_vin import _configuration
from tests.token_support import dealer_group_of
from tests.vin_support import unique_vin


def _token() -> str:
    tenant_id = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
    )


def _post(client, token: str, **body):
    return client.post(
        "/v1/valuations",
        json={"finalOffer": "12500.00", "source": "manual", **body},
        headers={"Authorization": f"Bearer {token}"},
    )


def _count(db_session) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(Valuation))


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        ({}, ["vin", "vehicleMake", "vehicleModel"]),
        ({"vehicleMake": "VW", "vehicleModel": "Golf"}, ["vin"]),
        ({"vin": "WVWZZZ1KZAW574001", "vehicleModel": "Golf"}, ["vehicleMake"]),
        ({"vin": "WVWZZZ1KZAW574001", "vehicleMake": "VW"}, ["vehicleModel"]),
        ({"vin": "WVWZZZ1KZAW574001", "vehicleMake": "  ", "vehicleModel": "Golf"}, ["vehicleMake"]),
    ],
)
def test_a_valuation_without_its_car_is_refused_and_stores_nothing(client, db_session, body, missing):
    refused = _post(client, _token(), **body)

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "valuation_names_no_car", "fields": missing}
    assert _count(db_session) == 0


def test_a_named_car_is_accepted(client, db_session):
    created = _post(client, _token(), vin="wvwzzz1kzaw574002", vehicleMake="VW", vehicleModel="Golf")

    assert created.status_code == 201, created.text
    assert created.json()["vehicleVin"] == "WVWZZZ1KZAW574002"
    assert (created.json()["vehicleMake"], created.json()["vehicleModel"]) == ("VW", "Golf")


def test_a_pre_1981_chassis_number_counts_as_the_vin(client, db_session):
    created = _post(
        client, _token(), vin="1302412345", vehicleFirstRegistration="1972-05-01",
        vehicleMake="VW", vehicleModel="Käfer",
    )

    assert created.status_code == 201, created.text


def test_the_configuration_names_the_car_when_it_carries_a_vin(client, db_session):
    token = _token()
    configuration_id = _configuration(db_session, token, vin=unique_vin())

    created = _post(client, token, configurationId=configuration_id)

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["vehicleVin"] is not None
    assert (body["vehicleMake"], body["vehicleModel"]) == ("Škoda", "Octavia")


def test_a_configuration_without_a_vin_needs_one_typed_beside_it(client, db_session):
    token = _token()
    configuration_id = _configuration(db_session, token, vin=None)

    refused = _post(client, token, configurationId=configuration_id)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "valuation_names_no_car", "fields": ["vin"]}

    vin = unique_vin()
    created = _post(client, token, configurationId=configuration_id, vin=vin)
    assert created.status_code == 201, created.text
    assert created.json()["vehicleVin"] == vin
