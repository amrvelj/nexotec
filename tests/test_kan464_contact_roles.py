"""KAN-464 PR-1 — contacts hold roles (Anto, 2026-10-26: one register, roles;
Q1 of 2026-11-14, A: the lifecycle status, the credit block and the advisor
belong to the customer role; everything else to every contact).

- every customer migrated as `customer`; a create without `roles` is a customer;
- a supplier-only contact carries no customer field: no defaulted advisor, no
  prospect / inactive, no credit block, never in the unassigned queue;
- taking the customer role away closes those fields in the same audited write,
  and is refused while a credit block stands;
- the list filters by role; the lifecycle filters on a customer value match
  only customers; a merge leaves the survivor holding every role.
"""

import importlib.util
import pathlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.reconciliation import StateCheck, find_rows_in_state
from app.customer.models.customer import (
    ContactRole,
    Customer,
    CustomerLifecycleStatus,
    CustomerRole,
    CustomerType,
    Language,
)
from app.customer.reconciliation import CHECKS
from tests.membership_support import bearer, create_dealership, create_user, token

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "alembic/versions/customer/a4c1e6b9d2f7_kan464_customer_role.py"
_spec = importlib.util.spec_from_file_location("kan464_migration", _MIGRATION)
assert _spec and _spec.loader
_migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)


def _manager(dealership: str, user_id: str | None = None) -> dict[str, str]:
    return bearer(token(tenant_id=dealership, is_dealer_manager=True, user_id=user_id))


def _create(client, dealership, *, actor: str | None = None, **fields):
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        **fields,
    }
    return client.post(
        "/v1/customers", json=payload, headers={**_manager(dealership, actor), "Idempotency-Key": str(uuid.uuid4())}
    )


def _created(client, dealership, *, actor: str | None = None, **fields) -> dict:
    response = _create(client, dealership, actor=actor, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client, dealership, customer, **body):
    return client.patch(
        f"/v1/customers/{customer['id']}",
        json=body,
        headers={**_manager(dealership), "If-Match": str(customer["version"])},
    )


def _get(client, dealership, customer_id) -> dict:
    response = client.get(f"/v1/customers/{customer_id}", headers=_manager(dealership))
    assert response.status_code == 200, response.text
    return response.json()


def _list_ids(client, dealership, **params) -> set[str]:
    response = client.get("/v1/customers", params=params, headers=_manager(dealership))
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _open_rows(engine, customer_id) -> list[CustomerRole]:
    session = sessionmaker(bind=engine)()
    try:
        return list(
            session.scalars(
                select(CustomerRole).where(CustomerRole.customer_id == uuid.UUID(customer_id)).order_by(CustomerRole.role)
            ).all()
        )
    finally:
        session.close()


# --- the migration ----------------------------------------------------------------------


def test_the_migration_gives_every_existing_customer_the_customer_role(db_session):
    """Exit criterion 2: existing customers migrate as `customer` — merged
    ones too, valid from their own creation; a second run writes nothing."""

    group = uuid.uuid4()
    customers = []
    for status in (CustomerLifecycleStatus.ACTIVE, CustomerLifecycleStatus.MERGED):
        customer = Customer(
            group_id=group,
            customer_number=f"K-{uuid.uuid4().hex[:6]}",
            customer_type=CustomerType.INDIVIDUAL,
            language=Language.DE,
            first_name="Ada",
            last_name="Lovelace",
            lifecycle_status=status,
        )
        db_session.add(customer)
        customers.append(customer)
    db_session.flush()

    assert _migration.backfill_customer_role(db_session.connection()) == 2
    rows = db_session.scalars(select(CustomerRole).where(CustomerRole.group_id == group)).all()
    assert sorted((row.customer_id, row.role, row.valid_to) for row in rows) == sorted(
        (customer.id, ContactRole.CUSTOMER, None) for customer in customers
    )
    by_customer = {row.customer_id: row for row in rows}
    for customer in customers:
        assert by_customer[customer.id].valid_from == customer.created_at
    assert _migration.backfill_customer_role(db_session.connection()) == 0


# --- create ---------------------------------------------------------------------------------


def test_a_create_without_roles_is_a_customer_as_before(client, engine):
    dealership = create_dealership(client)
    actor = str(uuid.uuid4())
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Muster",
            "language": "de",
            "emails": [{"emailType": "personal", "emailAddress": "anna@example.ch"}],
        },
        headers={**_manager(dealership, actor), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    customer = response.json()
    assert customer["roles"] == ["customer"]
    assert customer["lifecycleStatus"] == "prospect"
    rows = _open_rows(engine, customer["id"])
    assert [(row.role, row.valid_to) for row in rows] == [(ContactRole.CUSTOMER, None)]


def test_a_contact_holds_at_least_one_role(client):
    dealership = create_dealership(client)
    response = _create(client, dealership, roles=[])
    assert response.status_code == 422, response.text
    unknown = _create(client, dealership, roles=["partner"])
    assert unknown.status_code == 422, unknown.text


def test_a_supplier_only_contact_carries_no_customer_field(client):
    """Q1 A: no lifecycle status of its own (the neutral `active`), no
    advisor defaulted from the acting user (D-24 is a customer rule)."""

    dealership = create_dealership(client)
    # A real user of the Betrieb, so the D-24 default could resolve.
    actor = create_user(client, dealership)["id"]
    supplier = _created(client, dealership, actor=actor, roles=["supplier"])
    assert supplier["roles"] == ["supplier"]
    assert supplier["lifecycleStatus"] == "active"
    assert supplier["advisorId"] is None

    both = _created(client, dealership, actor=actor, roles=["supplier", "customer", "supplier"])
    assert both["roles"] == ["customer", "supplier"]
    assert both["lifecycleStatus"] == "prospect"
    assert both["advisorId"] == actor


def test_a_supplier_only_contact_is_refused_a_customer_field_on_create(client):
    dealership = create_dealership(client)
    for field, value, refused in (
        ("lifecycleStatus", "prospect", "lifecycleStatus"),
        ("lifecycleStatus", "inactive", "lifecycleStatus"),
        ("advisorId", str(uuid.uuid4()), "advisorId"),
    ):
        response = _create(client, dealership, roles=["supplier"], **{field: value})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"] == {"reason": "customer_role_required", "field": refused}
    # Do-not-contact belongs to every contact.
    dnc = _created(client, dealership, roles=["supplier"], lifecycleStatus="do_not_contact")
    assert dnc["lifecycleStatus"] == "do_not_contact"


# --- update -----------------------------------------------------------------------------------


def test_giving_and_taking_a_role_keeps_its_history_and_is_audited(client, engine):
    dealership = create_dealership(client)
    customer = _created(client, dealership)
    gained = _patch(client, dealership, customer, roles=["customer", "supplier"])
    assert gained.status_code == 200, gained.text
    assert gained.json()["roles"] == ["customer", "supplier"]

    dropped = _patch(client, dealership, gained.json(), roles=["customer"])
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["roles"] == ["customer"]

    rows = _open_rows(engine, customer["id"])
    supplier_rows = [row for row in rows if row.role == ContactRole.SUPPLIER]
    assert len(supplier_rows) == 1 and supplier_rows[0].valid_to is not None
    assert [row.valid_to for row in rows if row.role == ContactRole.CUSTOMER] == [None]

    session = sessionmaker(bind=engine)()
    try:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_id == uuid.UUID(customer["id"]), AuditEvent.action == "update")
            .order_by(AuditEvent.id)
        ).all()
    finally:
        session.close()
    assert [(e.before["roles"], e.after["roles"]) for e in events] == [
        (["customer"], ["customer", "supplier"]),
        (["customer", "supplier"], ["customer"]),
    ]


def test_roles_cannot_be_cleared(client):
    dealership = create_dealership(client)
    customer = _created(client, dealership)
    for value in ([], None):
        response = _patch(client, dealership, customer, roles=value)
        assert response.status_code == 422, response.text


def test_taking_the_customer_role_away_closes_the_customer_fields(client, engine):
    """Prospect becomes the neutral `active`, the advisor is cleared — in the
    same audited write."""

    dealership = create_dealership(client)
    actor = create_user(client, dealership)["id"]
    response = client.post(
        "/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Muster",
            "language": "de",
            "lifecycleStatus": "inactive",
            "roles": ["customer", "supplier"],
            "emails": [{"emailType": "personal", "emailAddress": "anna@example.ch"}],
        },
        headers={**_manager(dealership, actor), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    customer = response.json()
    assert customer["advisorId"] == actor  # D-24 for the customer role

    patched = _patch(client, dealership, customer, roles=["supplier"])
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["roles"] == ["supplier"]
    assert body["lifecycleStatus"] == "active"
    assert body["advisorId"] is None and body["advisorLabel"] is None

    session = sessionmaker(bind=engine)()
    try:
        event = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_id == uuid.UUID(customer["id"]), AuditEvent.action == "update")
            .order_by(AuditEvent.id.desc())
        ).first()
    finally:
        session.close()
    assert event is not None
    assert event.before["roles"] == ["customer", "supplier"] and event.after["roles"] == ["supplier"]
    assert event.before["lifecycle_status"] == "inactive" and event.after["lifecycle_status"] == "active"
    assert "advisor_id" in event.before and event.after["advisor_id"] is None


def test_a_do_not_contact_contact_stays_so_when_it_loses_the_customer_role(client):
    dealership = create_dealership(client)
    customer = _created(client, dealership, roles=["customer", "supplier"], lifecycleStatus="do_not_contact")
    patched = _patch(client, dealership, customer, roles=["supplier"])
    assert patched.status_code == 200, patched.text
    assert patched.json()["lifecycleStatus"] == "do_not_contact"


def test_a_supplier_only_contact_is_refused_a_customer_field_on_update(client):
    dealership = create_dealership(client)
    supplier = _created(client, dealership, roles=["supplier"])
    for body, field in (
        ({"lifecycleStatus": "prospect"}, "lifecycleStatus"),
        ({"lifecycleStatus": "inactive"}, "lifecycleStatus"),
        ({"advisorId": str(uuid.uuid4())}, "advisorId"),
        # The same change with the role taken away in the same PATCH.
    ):
        response = _patch(client, dealership, supplier, **body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"] == {"reason": "customer_role_required", "field": field}
    both = _created(client, dealership, roles=["customer", "supplier"])
    response = _patch(client, dealership, both, roles=["supplier"], lifecycleStatus="prospect")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "customer_role_required"
    # Giving the role and a customer value together is fine.
    ok = _patch(client, dealership, supplier, roles=["customer", "supplier"], lifecycleStatus="prospect")
    assert ok.status_code == 200, ok.text
    assert ok.json()["lifecycleStatus"] == "prospect"


def test_a_credit_block_is_the_customer_roles(client):
    dealership = create_dealership(client)
    supplier = _created(client, dealership, roles=["supplier"])
    refused = client.post(
        f"/v1/customers/{supplier['id']}/credit-block",
        json={"blocked": True, "reason": "Offene Rechnungen"},
        headers={**_manager(dealership), "If-Match": str(supplier["version"]), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "customer_role_required", "field": "creditBlock"}

    customer = _created(client, dealership, roles=["customer", "supplier"])
    blocked = client.post(
        f"/v1/customers/{customer['id']}/credit-block",
        json={"blocked": True, "reason": "Offene Rechnungen"},
        headers={**_manager(dealership), "If-Match": str(customer["version"]), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert blocked.status_code == 200, blocked.text
    response = _patch(client, dealership, blocked.json(), roles=["supplier"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "customer_role_credit_blocked"
    assert _get(client, dealership, customer["id"])["roles"] == ["customer", "supplier"]


# --- the list ---------------------------------------------------------------------------------


def test_the_list_filters_by_role(client):
    dealership = create_dealership(client)
    customer = _created(client, dealership)
    supplier = _created(client, dealership, roles=["supplier"])
    both = _created(client, dealership, roles=["customer", "supplier"])

    assert _list_ids(client, dealership, role="supplier") == {supplier["id"], both["id"]}
    assert _list_ids(client, dealership, role="customer") == {customer["id"], both["id"]}
    assert _list_ids(client, dealership, roleNot="customer") == {supplier["id"]}
    assert _list_ids(client, dealership, roleNot="supplier") == {customer["id"]}
    response = client.get("/v1/customers", params={"role": "partner"}, headers=_manager(dealership))
    assert response.status_code == 422, response.text


def test_the_role_filter_does_not_reach_another_group(client):
    mine = create_dealership(client, "Garage Eins AG")
    theirs = create_dealership(client, "Garage Fremd AG")
    _created(client, theirs, roles=["supplier"])
    assert _list_ids(client, mine, role="supplier") == set()


def test_a_customer_lifecycle_filter_matches_customers_only(client):
    """A supplier stores the neutral `active` but has no lifecycle status:
    «Aktiv» does not list it; «ist nicht Aktiv» keeps it (ADR-102)."""

    dealership = create_dealership(client)
    active_customer = _created(client, dealership, lifecycleStatus="active")
    prospect = _created(client, dealership)
    supplier = _created(client, dealership, roles=["supplier"])
    dnc_supplier = _created(client, dealership, roles=["supplier"], lifecycleStatus="do_not_contact")

    assert _list_ids(client, dealership, lifecycle_status="active") == {active_customer["id"]}
    assert _list_ids(client, dealership, lifecycle_status_not="active") == {
        prospect["id"],
        supplier["id"],
        dnc_supplier["id"],
    }
    assert _list_ids(client, dealership, lifecycle_status="do_not_contact") == {dnc_supplier["id"]}
    assert _list_ids(client, dealership, lifecycle_status_not="do_not_contact") == {
        active_customer["id"],
        prospect["id"],
        supplier["id"],
    }


def test_a_supplier_never_enters_the_unassigned_queue(client):
    dealership = create_dealership(client)
    supplier = _created(client, dealership, roles=["supplier"])
    customer = _created(client, dealership, roles=["customer"])
    cleared = _patch(client, dealership, customer, advisorId=None)
    assert cleared.status_code == 200, cleared.text
    assert _list_ids(client, dealership, unassigned="true") == {customer["id"]}
    assert supplier["id"] not in _list_ids(client, dealership, unassigned="true")


# --- merge and monitoring ----------------------------------------------------------------------


def test_a_merge_leaves_the_survivor_holding_every_role(client, engine):
    dealership = create_dealership(client)
    survivor = _created(client, dealership, roles=["supplier"])
    duplicate = _created(client, dealership, roles=["customer", "supplier"])
    merged = client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_manager(dealership), "If-Match": str(duplicate["version"]), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert merged.status_code == 200, merged.text
    assert _get(client, dealership, survivor["id"])["roles"] == ["customer", "supplier"]

    rows = _open_rows(engine, survivor["id"])
    open_rows = [row.role for row in rows if row.valid_to is None]
    assert sorted(open_rows, key=lambda role: role.value) == [ContactRole.CUSTOMER, ContactRole.SUPPLIER]
    # The duplicate's supplier row moved as history, closed: one open row per role.
    assert len([row for row in rows if row.role == ContactRole.SUPPLIER]) == 2
    assert _open_rows(engine, duplicate["id"]) == []


def _no_role_check() -> StateCheck:
    return next(c for c in CHECKS if isinstance(c, StateCheck) and c.label == "customer holds no open customer_role")


def test_reconciliation_reports_a_contact_without_a_role(client, engine):
    dealership = create_dealership(client)
    kept = _created(client, dealership)
    lost = _created(client, dealership)
    session = sessionmaker(bind=engine)()
    try:
        for row in session.scalars(select(CustomerRole).where(CustomerRole.customer_id == uuid.UUID(lost["id"]))):
            session.delete(row)
        session.commit()
        found = {row_id for row_id, _ in find_rows_in_state(session, _no_role_check())}
    finally:
        session.close()
    assert uuid.UUID(lost["id"]) in found
    assert uuid.UUID(kept["id"]) not in found
