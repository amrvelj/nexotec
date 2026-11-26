"""KAN-810 (Anto, 2026-11-26, Q1 option 1): «Gesperrt» on the contact.

A block of its own, beside the credit block: a workshop order refuses a
«Gesperrt» contact as its customer or invoice recipient, while the credit
block only changes how such an order is paid. Shaped like the credit block
(the follow-up answer of the same day): placed with a reason, it records
when, it is audited, it is the customer role's data and written by who may
write that role (`contact_customer_role`).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.contact import public
from app.contact.models.contact import Contact
from app.contact.reconciliation import CHECKS
from app.contact.schemas.contact import ContactCreate, ContactEmailCreate
from app.contact.services.contact import create_contact, set_block
from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.errors import ConflictError
from app.core.reconciliation import StateCheck, find_rows_in_state
from tests.membership_support import bearer, create_dealership, token


def _contact(db_session) -> Contact:
    return create_contact(
        db_session,
        group_id=uuid.uuid4(),
        data=ContactCreate(
            contact_type="individual",
            language="de",
            first_name="Erich",
            last_name="Schneider",
            emails=[ContactEmailCreate(email_type="personal", email_address="erich@example.ch", is_primary=True)],
        ),
        actor_id=uuid.uuid4(),
        acting_dealership_id=None,
        dealership_id=uuid.uuid4(),
    )


def _as(dealership: str, role: AccessRole | None = None) -> dict[str, str]:
    return bearer(token(role, tenant_id=dealership, is_dealer_manager=role is None))


def _created(client, headers, **fields) -> dict:
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        **fields,
    }
    response = client.post("/v1/contacts", json=payload, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


def _block(client, headers, contact: dict, blocked: bool, reason: str | None = "Hausverbot"):
    return client.post(
        f"/v1/contacts/{contact['id']}/block",
        json={"blocked": blocked, "reason": reason if blocked else None},
        headers={**headers, "If-Match": str(contact["version"]), "Idempotency-Key": str(uuid.uuid4())},
    )


# --- the service ------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_placing_the_block_needs_a_reason(db_session, reason):
    contact = _contact(db_session)
    with pytest.raises(ConflictError) as refused:
        set_block(db_session, contact=contact, blocked=True, reason=reason, actor_id=uuid.uuid4(), acting_dealership_id=None)
    assert refused.value.details == {"reason": "contact_block_reason_required"}
    assert contact.blocked is False


def test_block_and_lift_record_when_and_why_and_are_audited(db_session):
    contact = _contact(db_session)
    actor = uuid.uuid4()

    blocked = set_block(
        db_session, contact=contact, blocked=True, reason="  Hausverbot  ", actor_id=actor, acting_dealership_id=None
    )
    assert (blocked.blocked, blocked.blocked_reason) == (True, "Hausverbot")
    assert blocked.blocked_at is not None
    # A block of its own: the credit block is untouched.
    assert blocked.credit_block is False

    lifted = set_block(db_session, contact=blocked, blocked=False, reason=None, actor_id=actor, acting_dealership_id=None)
    assert (lifted.blocked, lifted.blocked_reason, lifted.blocked_at) == (False, None, None)

    events = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_id == contact.id, AuditEvent.action == "block_changed")
        .order_by(AuditEvent.id)
    ).all()
    assert [(e.before, e.after) for e in events] == [
        ({"blocked": False, "blockedReason": None}, {"blocked": True, "blockedReason": "Hausverbot"}),
        ({"blocked": True, "blockedReason": "Hausverbot"}, {"blocked": False, "blockedReason": None}),
    ]
    assert {e.actor_id for e in events} == {actor}


def test_contact_public_hands_the_block_to_another_context(db_session):
    contact = _contact(db_session)
    set_block(db_session, contact=contact, blocked=True, reason="Hausverbot", actor_id=uuid.uuid4(), acting_dealership_id=None)

    ref = public.get_contact_or_404(db_session, contact.group_id, contact.id)

    assert (ref.blocked, ref.blocked_reason) == (True, "Hausverbot")
    assert (ref.credit_block, ref.credit_block_reason) == (False, None)


# --- the API ----------------------------------------------------------------------------------


def test_the_api_places_and_lifts_the_block(client):
    dealership = create_dealership(client)
    manager = _as(dealership)
    contact = _created(client, manager)

    missing = _block(client, manager, contact, True, reason=None)
    assert missing.status_code == 409, missing.text
    assert missing.json()["error"]["details"]["reason"] == "contact_block_reason_required"

    stale = _block(client, manager, {**contact, "version": contact["version"] + 1}, True)
    assert stale.status_code == 409, stale.text

    blocked = _block(client, manager, contact, True)
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert (body["blocked"], body["blockedReason"], body["creditBlock"]) == (True, "Hausverbot", False)
    assert body["blockedAt"] is not None

    lifted = _block(client, manager, body, False)
    assert lifted.status_code == 200, lifted.text
    assert (lifted.json()["blocked"], lifted.json()["blockedReason"], lifted.json()["blockedAt"]) == (False, None, None)


def test_another_groups_contact_is_a_404(client):
    contact = _created(client, _as(create_dealership(client)))
    response = _block(client, _as(create_dealership(client)), contact, True)
    assert response.status_code == 404, response.text


def test_the_block_is_written_by_sales_and_aftersales_only(client):
    dealership = create_dealership(client)
    sales = _as(dealership, AccessRole.SALES)
    aftersales = _as(dealership, AccessRole.AFTERSALES)
    inventory = _as(dealership, AccessRole.INVENTORY)
    contact = _created(client, sales)

    refused = _block(client, inventory, contact, True)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"] == {"reason": "customer_role_not_permitted", "field": "blocked"}

    blocked = _block(client, aftersales, contact, True)
    assert blocked.status_code == 200, blocked.text
    refused = _block(client, inventory, blocked.json(), False)
    assert refused.status_code == 403, refused.text
    assert client.get(f"/v1/contacts/{contact['id']}", headers=sales).json()["blocked"] is True


def test_the_block_is_the_customer_roles(client):
    dealership = create_dealership(client)
    manager = _as(dealership)
    supplier = _created(client, manager, roles=["supplier"])

    refused = _block(client, manager, supplier, True)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "customer_role_required", "field": "blocked"}

    contact = _created(client, manager, roles=["customer", "supplier"])
    blocked = _block(client, manager, contact, True).json()
    response = client.patch(
        f"/v1/contacts/{contact['id']}",
        json={"roles": ["supplier"]},
        headers={**manager, "If-Match": str(blocked["version"])},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "customer_role_blocked"
    assert client.get(f"/v1/contacts/{contact['id']}", headers=manager).json()["roles"] == ["customer", "supplier"]


def test_reconciliation_reports_a_blocked_contact_without_the_customer_role(client, engine):
    dealership = create_dealership(client)
    manager = _as(dealership)
    supplier = _created(client, manager, roles=["supplier"])
    customer = _created(client, manager)
    assert _block(client, manager, customer, True).status_code == 200
    check = next(
        c for c in CHECKS
        if isinstance(c, StateCheck) and c.label == "contact without the customer role carries a customer field"
    )
    session = sessionmaker(bind=engine)()
    try:
        row = session.get(Contact, uuid.UUID(supplier["id"]))
        row.blocked, row.blocked_reason = True, "Hausverbot"
        session.commit()
        found = {row_id for row_id, _ in find_rows_in_state(session, check)}
    finally:
        session.close()
    assert uuid.UUID(supplier["id"]) in found
    assert uuid.UUID(customer["id"]) not in found
