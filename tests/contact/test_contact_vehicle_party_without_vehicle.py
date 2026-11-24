"""KAN-339: a vehicle-party row whose vehicle_id names no vehicle_mdm record
(a legacy `vehicle` id, or a row written outside the production path) never
answers 500. The Vehicles tab renders it from the row's own stored label
(KAN-84) — with a null VIN and vehicle number when there is none — and
contact reconciliation reports the row
(`test_reconciliation.py::test_contact_reconciliation_detects_orphaned_vehicle_party`).
"""

import datetime as dt
import uuid

from app.contact.models.contact import Contact, ContactType, Language
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow


def _headers(group_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def _contact(db_session, group_id: uuid.UUID) -> Contact:
    contact = Contact(
        group_id=group_id, contact_number=f"K-{uuid.uuid4().hex[:6]}", contact_type=ContactType.INDIVIDUAL,
        language=Language.DE, first_name="Anna", last_name="Muster",
    )
    db_session.add(contact)
    db_session.commit()
    return contact


def _dangling_party(db_session, contact: Contact, **label) -> VehicleParty:
    party = VehicleParty(
        vehicle_id=uuid.uuid4(), contact_id=contact.id, role=VehiclePartyRole.OWNER,
        effective_from=utcnow() - dt.timedelta(days=3), **label,
    )
    db_session.add(party)
    db_session.commit()
    return party


def test_a_party_with_no_vehicle_and_no_stored_label_renders_with_a_null_vin(client, db_session):
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    party = _dangling_party(db_session, contact)

    response = client.get(f"/v1/contacts/{contact.id}/vehicles", headers=_headers(group_id))

    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert row["id"] == str(party.id)
    assert row["vehicle"] == {
        "id": str(party.vehicle_id), "vin": None, "vehicleNumber": None, "make": None, "model": None,
        "modelYear": None, "trim": None,
    }


def test_a_party_with_no_vehicle_renders_its_stored_label(client, db_session):
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    _dangling_party(
        db_session, contact, vehicle_vin="WVWZZZ1KZAW339001", vehicle_number="F-000339",
        vehicle_make="Volkswagen", vehicle_model="Golf", vehicle_label_refreshed_at=utcnow(),
    )

    response = client.get(
        f"/v1/contacts/{contact.id}/vehicles", params={"includeClosed": "true"}, headers=_headers(group_id)
    )

    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert row["vehicle"]["vin"] == "WVWZZZ1KZAW339001"
    assert row["vehicle"]["vehicleNumber"] == "F-000339"
    assert row["vehicle"]["make"] == "Volkswagen"


def test_a_write_to_a_party_with_no_vehicle_answers_without_a_500(client, db_session):
    """PATCH and Disconnect load the single row and build the response from
    its label: neither may fail for want of a vehicle-mdm record."""

    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    party = _dangling_party(db_session, contact)
    headers = _headers(group_id)

    patched = client.patch(
        f"/v1/contacts/{contact.id}/vehicles/{party.id}",
        json={"effectiveTo": (utcnow() + dt.timedelta(days=30)).isoformat()},
        headers=headers,
    )
    disconnected = client.delete(f"/v1/contacts/{contact.id}/vehicles/{party.id}", headers=headers)

    assert patched.status_code == 200, patched.text
    assert patched.json()["vehicle"]["vin"] is None
    assert disconnected.status_code == 204, disconnected.text
