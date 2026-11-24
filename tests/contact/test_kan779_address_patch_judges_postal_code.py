"""KAN-779: a contact address PATCH judges the merged postal code against the merged country.

`ContactAddressCreate` called `validate_postal_code_for_country` (D-11); `ContactAddressUpdate`
took any string up to 12 characters, and the service re-derived the canton (D-13) from it. A PATCH
now judges the code it would store against the country it would store — the create path's rule —
and only when it touches either, so a stored row from before the rule still saves an unrelated
field. KAN-778's house-number rule rides along: a legacy «12\\n» still reads and saves.
"""

import uuid

import pytest
import sqlalchemy as sa

from app.contact.models.contact import ContactAddress
from tests.contact.test_contact_canton import _bearer, _create_contact, _create_dealer, _token

_ZURICH = {
    "addressType": "domicile",
    "addressStreet": "Bahnhofstrasse",
    "addressHouseNumber": "1",
    "addressPostalCode": "8001",
    "addressLocality": "Zürich",
}


def _contact_with_address(client, address: dict | None = None) -> tuple[str, str, str]:
    dealer_id = _create_dealer(client)
    contact = _create_contact(client, dealer_id, addresses=[address or _ZURICH])
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    return token, contact["id"], contact["address"]["id"]


def _patch(client, token, contact_id, address_id, body):
    return client.patch(f"/v1/contacts/{contact_id}/addresses/{address_id}", json=body, headers=_bearer(token))


@pytest.mark.parametrize("bad", ["abc", "8001\n", "80011", "800"])
def test_a_bad_swiss_postal_code_on_patch_is_refused(client, bad):
    token, contact_id, address_id = _contact_with_address(client)

    response = _patch(client, token, contact_id, address_id, {"addressPostalCode": bad})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "contact_address_postal_code_invalid"
    stored = client.get(f"/v1/contacts/{contact_id}", headers=_bearer(token)).json()["address"]
    assert (stored["addressPostalCode"], stored["addressCanton"]) == ("8001", "ZH")


def test_a_good_postal_code_on_patch_saves_and_rederives_the_canton(client):
    token, contact_id, address_id = _contact_with_address(client)

    response = _patch(client, token, contact_id, address_id, {"addressPostalCode": "3011"})

    assert response.status_code == 200, response.text
    assert (response.json()["addressPostalCode"], response.json()["addressCanton"]) == ("3011", "BE")


def test_a_country_change_alone_rejudges_the_stored_code(client):
    """A German «10115» is plausible for DE; turning the country to CH alone re-judges it
    under the Swiss 4-digit rule, and a PATCH carrying both saves."""

    token, contact_id, address_id = _contact_with_address(
        client, {**_ZURICH, "addressPostalCode": "10115", "addressLocality": "Berlin", "addressCountry": "DE"}
    )

    refused = _patch(client, token, contact_id, address_id, {"addressCountry": "CH"})
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == "contact_address_postal_code_invalid"

    both = _patch(client, token, contact_id, address_id, {"addressCountry": "CH", "addressPostalCode": "8001"})
    assert both.status_code == 200, both.text
    assert (both.json()["addressCountry"], both.json()["addressCanton"]) == ("CH", "ZH")


def test_an_unrelated_patch_on_a_legacy_row_saves(client, db_session):
    """A row stored before KAN-779/KAN-778 (a bad postal code, a house number with a newline)
    still reads and saves a field the PATCH touches; the address block is judged only when the
    PATCH touches it."""

    token, contact_id, address_id = _contact_with_address(client)
    db_session.execute(
        sa.update(ContactAddress)
        .where(ContactAddress.id == uuid.UUID(address_id))
        .values(address_postal_code="abc", address_house_number="12\n")
    )
    db_session.commit()

    read = client.get(f"/v1/contacts/{contact_id}", headers=_bearer(token))
    assert read.status_code == 200, read.text
    assert read.json()["address"]["addressHouseNumber"] == "12\n"

    saved = _patch(client, token, contact_id, address_id, {"label": "Zuhause"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["label"] == "Zuhause"

    touched = _patch(client, token, contact_id, address_id, {"addressHouseNumber": "12\n"})
    assert touched.status_code == 422, touched.text
