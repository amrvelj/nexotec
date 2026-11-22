"""KAN-789: a null on a required field of a customer address, phone or e-mail PATCH is a 422.

The three child-row PATCH schemas type every field `X | None = None` for PATCH semantics and the
services `setattr` every field sent, so `{"addressStreet": null}` reached the flush and the
database's NOT NULL turned it into a 500. The schemas now refuse a null on any field whose column
is NOT NULL, naming it; a null on a nullable column still clears it.

The not-null fields are derived from the models' columns, so a new required column cannot slip.
"""

import uuid

import pytest
from pydantic import BaseModel

from app.customer.models.customer import CustomerAddress, CustomerEmail, CustomerPhone
from app.customer.schemas.customer import CustomerAddressUpdate, CustomerEmailUpdate, CustomerPhoneUpdate
from tests.customer.test_customer_canton import _bearer, _create_customer, _create_dealer, _token

_CHANNELS: dict[str, tuple[type, type[BaseModel]]] = {
    "addresses": (CustomerAddress, CustomerAddressUpdate),
    "phones": (CustomerPhone, CustomerPhoneUpdate),
    "emails": (CustomerEmail, CustomerEmailUpdate),
}


def _split(channel: str) -> tuple[list[str], list[str]]:
    model, schema = _CHANNELS[channel]
    columns = {column.name: column.nullable for column in model.__table__.columns}
    assert set(schema.model_fields) <= set(columns), "every PATCH field is a column"
    camel = {name: field.alias or name for name, field in schema.model_fields.items()}
    required = [camel[name] for name in schema.model_fields if columns[name] is False]
    nullable = [camel[name] for name in schema.model_fields if columns[name] is True]
    return required, nullable


_CASES = [(channel, field) for channel in _CHANNELS for field in _split(channel)[0]]
_CLEARABLE = [(channel, field) for channel in _CHANNELS for field in _split(channel)[1]]


def _row(client, channel: str) -> tuple[str, str, str]:
    dealer_id = _create_dealer(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    customer = _create_customer(client, dealer_id)
    body = {
        "addresses": {
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern",
        },
        "phones": {"phoneType": "mobile", "phoneE164": "+41791234567"},
        "emails": {"emailType": "work", "emailAddress": f"x-{uuid.uuid4().hex[:8]}@example.ch"},
    }[channel]
    created = client.post(f"/v1/customers/{customer['id']}/{channel}", json=body, headers=_bearer(token))
    assert created.status_code == 201, created.text
    return token, customer["id"], created.json()["id"]


def test_the_required_fields_are_the_ones_the_ticket_names():
    """The derivation is not vacuous."""

    assert _split("addresses")[0] == [
        "addressType", "addressStreet", "addressHouseNumber", "addressPostalCode", "addressLocality",
        "addressCountry", "isPrimary", "doNotUse", "consentGranted",
    ]
    assert _split("phones")[0] == ["phoneType", "phoneE164", "isPrimary", "doNotUse", "consentGranted"]
    assert _split("emails")[0] == ["emailType", "emailAddress", "isPrimary", "doNotUse", "consentGranted"]


@pytest.mark.parametrize(("channel", "field"), _CASES)
def test_a_null_on_a_required_field_is_a_422_naming_it(client, channel, field):
    token, customer_id, row_id = _row(client, channel)

    response = client.patch(f"/v1/customers/{customer_id}/{channel}/{row_id}", json={field: None}, headers=_bearer(token))

    assert response.status_code == 422, response.text
    assert field in response.text


@pytest.mark.parametrize(("channel", "field"), _CLEARABLE)
def test_a_null_on_a_nullable_field_still_clears_it(client, channel, field):
    token, customer_id, row_id = _row(client, channel)

    response = client.patch(f"/v1/customers/{customer_id}/{channel}/{row_id}", json={field: None}, headers=_bearer(token))

    assert response.status_code == 200, response.text
    assert response.json()[field] is None
