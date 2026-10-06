"""KAN-86 step 1: every enum column reads, and every enum filter matches,
both the member NAME and its .value (app/core/enum_type.py).

Rows in the value form already exist (KAN-91's consent_source rows, any
insert that relied on a migration's lowercase server default), and from
KAN-86 step 2 on every rewritten row is in it, while code from this step is
still serving. Each test seeds the value form with raw SQL — the ORM can
only write names in this step — and goes through a real endpoint.
"""

import os
import uuid

import pytest
from sqlalchemy import select, text

from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_worker import poll_once
from app.customer.models.customer import ConsentSource, Customer, CustomerEmail

# Raw SQL here is Postgres's (`::text`, `interval`); the lane of record runs it.
pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _token(tenant_id: uuid.UUID, role: AccessRole | None = None, *, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealership(client) -> uuid.UUID:
    payload = {
        "legalName": "Garage Musterbetrieb AG", "dealerLicenseNumber": f"ZH-{uuid.uuid4().hex[:6]}",
        "licenseState": "ZH", "franchiseType": "independent", "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
        "address": {
            "street": "Bahnhofstrasse", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"
        },
    }
    response = client.post(
        "/v1/dealerships", json=payload, headers=_bearer(_token(uuid.uuid4(), AccessRole.PLATFORM_ADMIN))
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _create_customer(client, tenant_id: uuid.UUID, first_name: str) -> dict:
    payload = {
        "firstName": first_name, "lastName": "Muster", "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"{first_name.lower()}-{uuid.uuid4().hex[:6]}@example.ch"}],
    }
    response = client.post(
        "/v1/customers", json=payload, headers=_bearer(_token(tenant_id, is_dealer_manager=True))
    )
    assert response.status_code == 201, response.text
    return response.json()


def _to_value_form(db_session, customer_id: str) -> None:
    """Rewrites one customer and its email row to the .value form — what
    KAN-86 step 2's migration (and, for consent_source, KAN-52's
    3c8f2a6b1e40) leaves behind."""

    db_session.execute(
        text(
            "UPDATE customer SET customer_type = 'individual', language = 'de', "
            "lifecycle_status = 'prospect', gender = 'unspecified' WHERE id = :id"
        ),
        {"id": customer_id},
    )
    db_session.execute(
        text(
            "UPDATE customer_email SET email_type = 'personal', consent_source = 'form', "
            "consent_scope = 'marketing' WHERE customer_id = :id"
        ),
        {"id": customer_id},
    )
    db_session.commit()


def test_seeded_rows_really_are_in_both_forms(client, db_session):
    tenant_id = _create_dealership(client)
    named = _create_customer(client, tenant_id, "Named")
    valued = _create_customer(client, tenant_id, "Valued")
    _to_value_form(db_session, valued["id"])

    stored = dict(
        db_session.execute(
            text("SELECT id::text, customer_type FROM customer WHERE id IN (:a, :b)"),
            {"a": named["id"], "b": valued["id"]},
        ).all()
    )
    assert stored == {named["id"]: "INDIVIDUAL", valued["id"]: "individual"}


def test_customer_list_returns_both_forms_with_their_projections(client, db_session):
    """GET /v1/customers hydrates Customer rows (list_customers) and then
    the phone/email/address rows (compute_customer_projections_batch, the
    path KAN-60's guard never covered). A value-form consent_source on an
    email row 500'd that second call before (KAN-91)."""

    tenant_id = _create_dealership(client)
    named = _create_customer(client, tenant_id, "Named")
    valued = _create_customer(client, tenant_id, "Valued")
    _to_value_form(db_session, valued["id"])

    response = client.get("/v1/customers", headers=_bearer(_token(tenant_id, AccessRole.SALES)))

    assert response.status_code == 200, response.text
    by_id = {item["id"]: item for item in response.json()["items"]}
    assert set(by_id) == {named["id"], valued["id"]}
    assert by_id[valued["id"]]["customerType"] == "individual"
    assert by_id[valued["id"]]["email"] == valued["email"]


def test_a_value_form_contact_row_alone_no_longer_500s_the_customer_list(client, db_session):
    """KAN-91's exact shape: the customer row is in the name form (so
    KAN-60's guard in list_customers lets it through), only its email row
    holds the lowercase consent_source 3c8f2a6b1e40 wrote. That row reaches
    compute_customer_projections_batch, which 500'd the whole page."""

    tenant_id = _create_dealership(client)
    customer = _create_customer(client, tenant_id, "Valued")
    db_session.execute(
        text("UPDATE customer_email SET consent_source = 'form' WHERE customer_id = :id"), {"id": customer["id"]}
    )
    db_session.commit()

    response = client.get("/v1/customers", headers=_bearer(_token(tenant_id, AccessRole.SALES)))

    assert response.status_code == 200, response.text
    assert [(i["id"], i["email"]) for i in response.json()["items"]] == [(customer["id"], customer["email"])]


def test_customer_list_filters_match_both_forms(client, db_session):
    tenant_id = _create_dealership(client)
    named = _create_customer(client, tenant_id, "Named")
    valued = _create_customer(client, tenant_id, "Valued")
    _to_value_form(db_session, valued["id"])
    headers = _bearer(_token(tenant_id, AccessRole.SALES))

    for query in ({"customerType": "individual"}, {"language": "de"}, {"lifecycleStatus": "prospect"}):
        response = client.get("/v1/customers", params=query, headers=headers)
        assert response.status_code == 200, response.text
        assert {i["id"] for i in response.json()["items"]} == {named["id"], valued["id"]}, query
        assert response.json()["total"] == 2, query


def test_customer_detail_reads_the_value_form(client, db_session):
    tenant_id = _create_dealership(client)
    valued = _create_customer(client, tenant_id, "Valued")
    _to_value_form(db_session, valued["id"])

    headers = _bearer(_token(tenant_id, AccessRole.SALES))
    detail = client.get(f"/v1/customers/{valued['id']}", headers=headers)
    emails = client.get(f"/v1/customers/{valued['id']}/emails", headers=headers)

    assert detail.status_code == 200, detail.text
    assert detail.json()["lifecycleStatus"] == "prospect"
    assert emails.status_code == 200, emails.text
    assert [(e["emailType"], e["consentSource"]) for e in emails.json()["items"]] == [("personal", "form")]


def test_a_write_to_a_value_form_row_stores_the_name(client, db_session):
    """Step 1 writes names only; a row touched by this code goes back to the
    name form, which step 2's migration and step 3's sweep both rewrite."""

    tenant_id = _create_dealership(client)
    valued = _create_customer(client, tenant_id, "Valued")
    _to_value_form(db_session, valued["id"])

    customer = db_session.get(Customer, uuid.UUID(valued["id"]))
    email = db_session.scalars(select(CustomerEmail).where(CustomerEmail.customer_id == customer.id)).one()
    assert email.consent_source is ConsentSource.FORM
    email.consent_source = ConsentSource.WEB
    db_session.commit()

    stored = db_session.execute(
        text("SELECT consent_source FROM customer_email WHERE id = :id"), {"id": str(email.id)}
    ).scalar_one()
    assert stored == "WEB"


def test_stock_list_and_its_status_filter_read_both_forms(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _bearer(_token(tenant_id, AccessRole.INVENTORY))
    ids = []
    for label in ("Škoda Octavia", "VW Golf"):
        response = client.post(
            "/v1/inventory/stock-items", json={"vehicleLabel": label, "condition": "new"}, headers=headers
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    named_id, valued_id = ids
    before = db_session.execute(
        text("SELECT lifecycle_status, condition FROM stock_item WHERE id = :id"), {"id": valued_id}
    ).one()
    db_session.execute(
        text("UPDATE stock_item SET lifecycle_status = lower(lifecycle_status), condition = lower(condition) WHERE id = :id"),
        {"id": valued_id},
    )
    db_session.commit()
    lifecycle_value = before.lifecycle_status.lower()

    listed = client.get("/v1/inventory/stock-items", headers=headers)
    filtered = client.get("/v1/inventory/stock-items", params={"lifecycleStatus": lifecycle_value}, headers=headers)

    assert listed.status_code == 200, listed.text
    assert {i["id"] for i in listed.json()["items"]} == {named_id, valued_id}
    assert filtered.status_code == 200, filtered.text
    assert {i["id"] for i in filtered.json()["items"]} == {named_id, valued_id}
    assert {i["condition"] for i in listed.json()["items"]} == {"new"}


def test_the_outbox_poller_claims_pending_rows_in_both_forms(db_session):
    """The worker polls `status == PENDING`; during KAN-86 step 2's deploy
    the old worker must still claim rows the migration rewrote to 'pending',
    and leave published rows alone in either form."""

    delivered: list[uuid.UUID] = []

    class _Recording:
        def deliver(self, message: OutboxMessage) -> None:
            delivered.append(message.id)

    ids = []
    for stored in ("PENDING", "pending", "PUBLISHED", "published"):
        message_id = uuid.uuid4()
        ids.append(message_id)
        db_session.execute(
            text(
                "INSERT INTO outbox_message (id, event_type, event_version, occurred_at, producer, "
                "aggregate_type, aggregate_id, correlation_id, payload, status, attempts, next_attempt_at, "
                "created_at) VALUES (:id, 'kan86.test', 1, now(), 'test', 'test', :id, :id, '{}', :status, 0, "
                "now() - interval '1 minute', now())"
            ),
            {"id": str(message_id), "status": stored},
        )
    db_session.commit()

    result = poll_once(db_session, _Recording())

    assert sorted(delivered) == sorted(ids[:2])
    assert result.claimed == 2 and result.published == 2
    db_session.expire_all()
    statuses = [db_session.get(OutboxMessage, i).status for i in ids]
    assert statuses == [OutboxStatus.PUBLISHED] * 4
