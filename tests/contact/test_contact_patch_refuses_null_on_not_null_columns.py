"""KAN-394 — a contact PATCH that sends `null` for a NOT NULL column is a
422 naming the field, before anything is written; it used to reach the
database and come back as the generic 409 (KAN-390) or, for an audited
field, a 500.

Leaving the key out still keeps the value, and a real value still saves:
only the explicit null is refused. No field became required.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.contact.models.contact import Contact
from tests.contact.test_contact_vehicle import _bearer, _create_contact, _create_dealer, _token

# Every ContactUpdate field backed by a NOT NULL contact column, with a
# value the PATCH may set instead. gender keeps its own message ("set it to
# 'unspecified' instead").
NOT_NULL_FIELDS = {
    "language": "fr",
    "lifecycleStatus": "active",
    "marketingConsent": True,
    "newsletter": True,
    "vatRegistered": True,
    "vatStatusConfirmed": True,
    "gender": "female",
}


def _patch(client, dealer_id: str, contact: dict, body: dict):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    return client.patch(
        f"/v1/contacts/{contact['id']}",
        json=body,
        headers={**_bearer(token), "If-Match": str(contact["version"])},
    )


@pytest.mark.parametrize("field", sorted(NOT_NULL_FIELDS))
def test_an_explicit_null_on_a_not_null_column_is_422_naming_the_field(client, db_session, field):
    dealer_id = _create_dealer(client)
    contact = _create_contact(client, dealer_id)
    audit_rows_before = db_session.scalar(select(AuditEvent.id).order_by(AuditEvent.id.desc()).limit(1))

    response = _patch(client, dealer_id, contact, {field: None, "nextFollowUp": "2026-12-01"})

    assert response.status_code == 422, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert [e["loc"] for e in errors] == [["body", field]]
    assert field in errors[0]["msg"]

    db_session.expire_all()
    row = db_session.get(Contact, uuid.UUID(contact["id"]))
    assert row.version == contact["version"]
    assert row.next_follow_up is None  # nothing of the PATCH was written
    assert db_session.scalar(select(AuditEvent.id).order_by(AuditEvent.id.desc()).limit(1)) == audit_rows_before


@pytest.mark.parametrize("field", sorted(NOT_NULL_FIELDS))
def test_a_value_for_the_same_field_still_saves(client, field):
    dealer_id = _create_dealer(client)
    contact = _create_contact(client, dealer_id)

    response = _patch(client, dealer_id, contact, {field: NOT_NULL_FIELDS[field]})

    assert response.status_code == 200, response.text
    assert response.json()[field] == NOT_NULL_FIELDS[field]


def test_leaving_the_fields_out_keeps_their_values(client):
    dealer_id = _create_dealer(client)
    contact = _create_contact(client, dealer_id)

    response = _patch(client, dealer_id, contact, {"nextFollowUp": "2026-12-01"})

    assert response.status_code == 200, response.text
    body = response.json()
    for field in NOT_NULL_FIELDS:
        assert body[field] == contact[field]


def test_every_not_null_column_the_patch_can_write_refuses_null():
    """A NOT NULL column added to ContactUpdate later is covered without a
    second edit: the list above and the validator must both name it."""

    from pydantic import ValidationError

    from app.contact.schemas.contact import ContactUpdate

    columns = Contact.__table__.c
    not_null = {
        ContactUpdate.model_fields[name].alias
        for name in ContactUpdate.model_fields
        if name in columns and not columns[name].nullable
    }
    assert not_null == set(NOT_NULL_FIELDS)
    for field in not_null:
        with pytest.raises(ValidationError):
            ContactUpdate.model_validate({field: None})
