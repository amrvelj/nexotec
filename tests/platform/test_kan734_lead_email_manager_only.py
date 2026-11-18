"""KAN-734 (Anto, 2026-11-15, KAN-225 Q3 A): a location's lead e-mail has one
rule on both screens — the manager only. The inventory role keeps writing the
rest of the publishing profile, lead routing's other fields included (they
share the PUT: the same e-mail sent back is no change)."""

import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.platform.organisation.models.dealership import Location
from tests.platform.test_dealership import _create_dealership
from tests.platform.test_kan225_publishing_profile import _manager, _profile, _role, _seat, _url

_ROUTING = {"leadPhone": "+41 44 000 00 00", "createsTask": True, "recipientRole": "sales"}


def _put_routing(client, dealership_id: str, location_id: str, body: dict, headers: dict):
    version = _profile(client, dealership_id)["version"]
    return client.put(
        _url(dealership_id, f"/locations/{location_id}/lead-routing"),
        json=body,
        headers={**headers, "If-Match": str(version)},
    )


def _patch_location(client, dealership_id: str, location: dict, body: dict, headers: dict):
    return client.patch(
        f"/v1/dealerships/{dealership_id}/locations/{location['id']}",
        json=body,
        headers={**headers, "If-Match": str(location["version"])},
    )


def test_the_inventory_role_cannot_change_the_lead_email_on_the_publishing_screen(client, db_session):
    dealership_id = _create_dealership(client)["id"]
    seat = _seat(client, dealership_id)
    inventory = _role(dealership_id, AccessRole.INVENTORY)
    profile_before = _profile(client, dealership_id)

    refused = _put_routing(client, dealership_id, seat["id"], {**_ROUTING, "leadEmail": "leads@garage-muster.ch"}, inventory)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["reason"] == "lead_email_manager_only"
    # Nothing written: the location, its routing and the profile are as before.
    location = db_session.get(Location, uuid.UUID(seat["id"]))
    db_session.refresh(location)
    assert location.lead_email is None and location.version == seat["version"]
    after = _profile(client, dealership_id)
    assert after["version"] == profile_before["version"]
    assert after["leadRouting"][0]["leadPhone"] is None
    assert not db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "location", AuditEvent.entity_id == location.id, AuditEvent.action == "update")
    ).all()


def test_the_inventory_role_cannot_clear_the_lead_email_either(client):
    dealership_id = _create_dealership(client)["id"]
    seat = _seat(client, dealership_id)
    set_ = _put_routing(client, dealership_id, seat["id"], {**_ROUTING, "leadEmail": "leads@garage-muster.ch"}, _manager(dealership_id))
    assert set_.status_code == 200, set_.text

    refused = _put_routing(client, dealership_id, seat["id"], {**_ROUTING, "leadEmail": None}, _role(dealership_id, AccessRole.INVENTORY))

    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["reason"] == "lead_email_manager_only"


def test_the_inventory_role_still_writes_the_other_routing_fields_and_the_profile(client):
    dealership_id = _create_dealership(client)["id"]
    seat = _seat(client, dealership_id)
    manager = _manager(dealership_id)
    assert _put_routing(client, dealership_id, seat["id"], {**_ROUTING, "leadEmail": "leads@garage-muster.ch"}, manager).status_code == 200
    inventory = _role(dealership_id, AccessRole.INVENTORY)

    # The same e-mail sent back with other routing values is no change to it.
    routed = _put_routing(
        client,
        dealership_id,
        seat["id"],
        {"leadEmail": "leads@garage-muster.ch", "leadPhone": "+41 44 111 11 11", "createsTask": False, "recipientRole": "inventory"},
        inventory,
    )
    assert routed.status_code == 200, routed.text
    [routing] = routed.json()["leadRouting"]
    assert routing["leadEmail"] == "leads@garage-muster.ch"
    assert routing["leadPhone"] == "+41 44 111 11 11"
    assert routing["recipientRole"] == "inventory"

    # Another publishing field, the profile itself.
    profile = _profile(client, dealership_id)
    patched = client.patch(
        _url(dealership_id),
        json={"websiteDomain": "garage-muster.ch"},
        headers={**inventory, "If-Match": str(profile["version"])},
    )
    assert patched.status_code == 200, patched.text


def test_the_manager_changes_the_lead_email_on_both_screens(client, db_session):
    dealership_id = _create_dealership(client)["id"]
    seat = _seat(client, dealership_id)
    manager = _manager(dealership_id)

    on_publishing = _put_routing(client, dealership_id, seat["id"], {**_ROUTING, "leadEmail": "leads@garage-muster.ch"}, manager)
    assert on_publishing.status_code == 200, on_publishing.text
    assert on_publishing.json()["leadRouting"][0]["leadEmail"] == "leads@garage-muster.ch"

    location = _seat(client, dealership_id)
    on_locations = _patch_location(client, dealership_id, location, {"leadEmail": "anfragen@garage-muster.ch"}, manager)
    assert on_locations.status_code == 200, on_locations.text
    assert on_locations.json()["leadEmail"] == "anfragen@garage-muster.ch"


def test_the_inventory_role_cannot_change_it_on_the_locations_screen(client):
    dealership_id = _create_dealership(client)["id"]
    location = _seat(client, dealership_id)

    refused = _patch_location(client, dealership_id, location, {"leadEmail": "leads@garage-muster.ch"}, _role(dealership_id, AccessRole.INVENTORY))

    assert refused.status_code == 403
