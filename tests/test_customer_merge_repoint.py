"""Merge re-pointing (D-08): vehicle-party rows, transactions, phones,
emails and external IDs must follow the duplicate into the survivor, per
FR-09 and the PRD's own Risks section ("merge without re-pointing is worse
than no merge — treat as a correctness bug"). See test_customer.py for the
basic flag-flip merge tests; this file is scoped to the re-pointing behavior
specifically.
"""

import uuid

from sqlalchemy import func as sa_func
from sqlalchemy.orm import sessionmaker

from app.core.auth import AccessRole, create_access_token
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from tests.dealership_support import valid_uid

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=_tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "uid": valid_uid(),
    }
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_user(client, dealer_id: str, **overrides) -> dict:
    admin_token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "firstName": "Sam",
        "lastName": "Sales",
        "email": f"sam-{uuid.uuid4().hex[:8]}@example.ch",
        "role": "sales",
        "accessRoles": ["sales"],
        "isDealerManager": False,
        "authIdentityId": f"stub-sub-{uuid.uuid4()}",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealerships/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    payload.update(overrides)
    response = client.post("/v1/customers", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _random_vin() -> str:
    import random

    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choices(alphabet, k=17))


def _create_vehicle(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"vin": _random_vin(), "make": "Honda", "model": "Accord", "modelYear": 2020, "condition": "used"}
    payload.update(overrides)
    response = client.post("/v1/vehicles", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_transaction(engine, dealer_id: str, user: dict, customer: dict, vehicle: dict) -> dict:
    """WP-8 PR-7 (ADR-050/S-D12): `transaction` writes are retired at the
    service layer — this seeds directly via the ORM instead, exactly like
    seeding any other retired-but-still-real table, on a session bound to
    the same test engine `client` itself uses.
    """

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        transaction = Transaction(
            tenant_id=uuid.UUID(dealer_id),
            transaction_type=TransactionType.SALE,
            status=TransactionStatus.DRAFT,
            customer_id=uuid.UUID(customer["id"]),
            vehicle_id=uuid.UUID(vehicle["id"]),
            primary_user_id=uuid.UUID(user["id"]),
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)
        return {"id": str(transaction.id)}
    finally:
        session.close()


def _merge(client, dealer_id: str, duplicate_id: str, survivor_id: str):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{duplicate_id}/merge",
        json={"duplicateOfCustomerId": survivor_id},
        headers={**_bearer(token), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _add_phone(client, dealer_id: str, customer_id: str, phone_e164: str, **overrides):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"phoneType": "mobile", "phoneE164": phone_e164}
    payload.update(overrides)
    response = client.post(f"/v1/customers/{customer_id}/phones", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _add_email(client, dealer_id: str, customer_id: str, email_address: str, **overrides):
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {"emailType": "personal", "emailAddress": email_address}
    payload.update(overrides)
    response = client.post(f"/v1/customers/{customer_id}/emails", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


def _add_external_id(client, dealer_id: str, customer_id: str, system_name: str, external_id: str):
    token = _token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealer_id))
    payload = {"systemName": system_name, "externalId": external_id}
    response = client.post(f"/v1/customers/{customer_id}/external-ids", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- vehicle parties -----------------------------------------------------------


def test_merge_repoints_vehicle_party_to_survivor(client, db_session):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)

    party = VehicleParty(
        vehicle_id=uuid.UUID(vehicle["id"]), customer_id=uuid.UUID(duplicate["id"]), role=VehiclePartyRole.OWNER
    )
    db_session.add(party)
    db_session.commit()

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    # The merge happened on a different SQLAlchemy session (TestClient's
    # dependency-injected one) — db_session's identity map still holds the
    # pre-merge `party` object (expire_on_commit=False), so force a refetch.
    db_session.expire_all()
    remaining = db_session.query(VehicleParty).filter(VehicleParty.vehicle_id == uuid.UUID(vehicle["id"])).all()
    assert len(remaining) == 1
    assert remaining[0].customer_id == uuid.UUID(survivor["id"])


def test_merge_marks_an_already_closed_overlapping_copy_without_reclosing_it(client, db_session):
    """KAN-177 review: a duplicate's copy that was closed in the past is
    recorded history — it keeps its effective_to, takes the mark, and gets no
    second close: no vehicle_party_remove audit row, no second
    customer.vehicle_party.unlinked event. The merge's own audit event still
    lists it among the kept rows."""

    import datetime as dt

    from sqlalchemy import select

    from app.core.audit_model import AuditEvent
    from app.core.outbox_model import OutboxMessage

    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = client.post("/v1/vehicle-mdm", json={"vin": _random_vin()}, headers=_bearer(token))
    assert created.status_code == 200, created.text
    vehicle_id = uuid.UUID(created.json()["vehicle"]["id"])

    shared_effective_from = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    closed_at = dt.datetime(2025, 6, 1, tzinfo=dt.UTC)
    survivor_row = VehicleParty(
        vehicle_id=vehicle_id, customer_id=uuid.UUID(survivor["id"]), role=VehiclePartyRole.OWNER,
        effective_from=shared_effective_from,
    )
    already_closed = VehicleParty(
        vehicle_id=vehicle_id, customer_id=uuid.UUID(duplicate["id"]), role=VehiclePartyRole.OWNER,
        effective_from=shared_effective_from, effective_to=closed_at,
    )
    db_session.add_all([survivor_row, already_closed])
    db_session.commit()
    kept_id = already_closed.id
    audit_rows_before = db_session.scalar(
        select(sa_func.count()).select_from(AuditEvent).where(AuditEvent.action == "vehicle_party_remove")
    )
    unlinked_before = db_session.scalar(
        select(sa_func.count()).select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "customer.vehicle_party.unlinked")
    )

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    db_session.expire_all()
    kept = db_session.get(VehicleParty, kept_id)
    assert kept is not None
    assert kept.customer_id == uuid.UUID(duplicate["id"])
    assert kept.effective_to == closed_at  # recorded history, not re-closed at its start
    assert kept.merged_into_customer_id == uuid.UUID(survivor["id"])

    audit_rows_after = db_session.scalar(
        select(sa_func.count()).select_from(AuditEvent).where(AuditEvent.action == "vehicle_party_remove")
    )
    unlinked_after = db_session.scalar(
        select(sa_func.count()).select_from(OutboxMessage)
        .where(OutboxMessage.event_type == "customer.vehicle_party.unlinked")
    )
    assert audit_rows_after == audit_rows_before  # no second close
    assert unlinked_after == unlinked_before  # its unlink was already a published fact

    merge_event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "merge").order_by(AuditEvent.created_at.desc())
    )
    assert merge_event is not None
    assert merge_event.after["vehiclePartiesKeptClosed"] == 1
    assert merge_event.after["vehiclePartiesKeptClosedIds"] == [str(kept_id)]


def test_merge_keeps_overlapping_vehicle_party_closed_and_marked_merged(client, db_session):
    """KAN-177 (ADR-064, Anto's ruling 2026-10-12, option A): the same
    vehicle/role/effective_from already exists on the survivor, so the
    duplicate's copy cannot be re-pointed (uq_vehicle_party_scope spans
    customer_id). It is KEPT — never deleted — closed at its own start and
    marked with the survivor it was merged into, still on the duplicate's
    customer_id; the survivor's own row is untouched; a non-overlapping row
    re-points as before; and the merge's audit event names the kept rows.
    """

    import datetime as dt

    from sqlalchemy import select

    from app.core.audit_model import AuditEvent
    from app.core.outbox_model import OutboxMessage

    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    # A vehicle_mdm row (KAN-31), not the legacy `vehicle` the other tests
    # here seed transactions against: the timeline read below labels the
    # rows from vehicle_mdm (KAN-84) and 500s on an id it cannot find.
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    created = client.post("/v1/vehicle-mdm", json={"vin": _random_vin()}, headers=_bearer(token))
    assert created.status_code == 200, created.text
    vehicle_id = uuid.UUID(created.json()["vehicle"]["id"])

    shared_effective_from = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    survivor_row = VehicleParty(
        vehicle_id=vehicle_id,
        customer_id=uuid.UUID(survivor["id"]),
        role=VehiclePartyRole.OWNER,
        effective_from=shared_effective_from,
    )
    overlapping = VehicleParty(
        vehicle_id=vehicle_id,
        customer_id=uuid.UUID(duplicate["id"]),
        role=VehiclePartyRole.OWNER,
        effective_from=shared_effective_from,
    )
    non_overlapping = VehicleParty(
        vehicle_id=vehicle_id,
        customer_id=uuid.UUID(duplicate["id"]),
        role=VehiclePartyRole.KEEPER,
        effective_from=shared_effective_from,
    )
    db_session.add_all([survivor_row, overlapping, non_overlapping])
    db_session.commit()
    survivor_row_id, overlapping_id, non_overlapping_id = survivor_row.id, overlapping.id, non_overlapping.id
    survivor_row_updated_at = survivor_row.updated_at

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    db_session.expire_all()
    rows = {r.id: r for r in db_session.query(VehicleParty).filter(VehicleParty.vehicle_id == vehicle_id).all()}
    # Nothing was deleted — party history is never deleted (ADR-064).
    assert set(rows) == {survivor_row_id, overlapping_id, non_overlapping_id}

    kept = rows[overlapping_id]
    assert kept.customer_id == uuid.UUID(duplicate["id"])
    assert kept.effective_to == shared_effective_from
    assert kept.merged_into_customer_id == uuid.UUID(survivor["id"])

    untouched = rows[survivor_row_id]
    assert untouched.customer_id == uuid.UUID(survivor["id"])
    assert untouched.effective_to is None
    assert untouched.merged_into_customer_id is None
    assert untouched.updated_at == survivor_row_updated_at

    repointed = rows[non_overlapping_id]
    assert repointed.customer_id == uuid.UUID(survivor["id"])
    assert repointed.effective_to is None
    assert repointed.merged_into_customer_id is None

    # The merge's own audit event names the kept-and-closed rows, not "dropped".
    log = client.get(f"/v1/customers/{duplicate['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200, log.text
    merge_event = next(item for item in log.json()["items"] if item["action"] == "merge")
    assert merge_event["after"]["vehiclePartiesRepointed"] == 1
    assert merge_event["after"]["vehiclePartiesKeptClosed"] == 1
    assert merge_event["after"]["vehiclePartiesKeptClosedIds"] == [str(overlapping_id)]
    assert "vehiclePartiesDropped" not in merge_event["after"]

    # The close itself is audited and published like every other close.
    closes = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "vehicle_party_remove", AuditEvent.entity_id == uuid.UUID(duplicate["id"])
        )
    ).all()
    assert len(closes) == 1
    assert closes[0].after["effectiveTo"] == shared_effective_from.isoformat()
    assert closes[0].after["mergedIntoCustomerId"] == survivor["id"]
    unlinked = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.vehicle_party.unlinked")
    ).all()
    assert [m.aggregate_id for m in unlinked] == [overlapping_id]
    assert unlinked[0].payload["mergedIntoCustomerId"] == survivor["id"]

    # ADR-064 timeline reads: the kept row is history, not a current party.
    current = client.get(f"/v1/customers/{duplicate['id']}/vehicles", headers=_bearer(token))
    assert current.status_code == 200, current.text
    assert current.json()["items"] == []
    history = client.get(
        f"/v1/customers/{duplicate['id']}/vehicles", params={"include_closed": "true"}, headers=_bearer(token)
    )
    assert [item["id"] for item in history.json()["items"]] == [str(overlapping_id)]
    closed_at = history.json()["items"][0]["effectiveTo"]
    assert dt.datetime.fromisoformat(closed_at) == shared_effective_from


# --- transactions ----------------------------------------------------------------


def test_merge_repoints_transactions(client, engine):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(engine, dealer_id, user, duplicate, vehicle)

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/transactions/{txn['id']}", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json()["customerId"] == survivor["id"]


# --- sales_offer / sales_contract (WP-8 PR-7) -------------------------------------


def test_merge_repoints_sales_offers(client):
    """A merge must repoint the NEW tables too, not just the retired
    `transaction` table repoint_customer_transactions already handled
    (app.sales.public.repoint_customer_sales_records, wired into
    merge_customer)."""

    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    offer = client.post("/v1/sales/offers", headers=_bearer(token)).json()
    client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"customerId": duplicate["id"]},
        headers={**_bearer(token), "If-Match": str(offer["version"])},
    )

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    refetched = client.get(f"/v1/sales/offers/{offer['id']}", headers=_bearer(token))
    assert refetched.json()["customerId"] == survivor["id"]


# --- phones / emails ---------------------------------------------------------------


def test_merge_repoints_unique_phone_and_email(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_phone(client, dealer_id, duplicate["id"], "+41791112233")
    _add_email(client, dealer_id, duplicate["id"], "dup-only@example.ch")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phones = client.get(f"/v1/customers/{survivor['id']}/phones", headers=_bearer(token)).json()["items"]
    emails = client.get(f"/v1/customers/{survivor['id']}/emails", headers=_bearer(token)).json()["items"]
    assert "+41791112233" in {p["phoneE164"] for p in phones}
    assert "dup-only@example.ch" in {e["emailAddress"] for e in emails}
    # Exactly one primary each, even though the survivor gained rows.
    assert sum(1 for p in phones if p["isPrimary"]) == 1
    assert sum(1 for e in emails if e["isPrimary"]) == 1


def test_merge_dedupes_conflicting_phone_and_email(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id, emails=[{"emailType": "personal", "emailAddress": "shared@example.ch"}])
    duplicate = _create_customer(client, dealer_id, emails=[{"emailType": "personal", "emailAddress": "shared@example.ch"}])
    _add_phone(client, dealer_id, survivor["id"], "+41799998877")
    _add_phone(client, dealer_id, duplicate["id"], "+41799998877")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    phones = client.get(f"/v1/customers/{survivor['id']}/phones", headers=_bearer(token)).json()["items"]
    emails = client.get(f"/v1/customers/{survivor['id']}/emails", headers=_bearer(token)).json()["items"]
    assert [p["phoneE164"] for p in phones].count("+41799998877") == 1
    assert [e["emailAddress"] for e in emails].count("shared@example.ch") == 1
    assert sum(1 for p in phones if p["isPrimary"]) == 1
    assert sum(1 for e in emails if e["isPrimary"]) == 1


# --- external ids -------------------------------------------------------------------


def test_merge_repoints_external_id(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_external_id(client, dealer_id, duplicate["id"], "crm", "CRM-123")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    rows = client.get(f"/v1/customers/{survivor['id']}/external-ids", headers=_bearer(token)).json()["items"]
    assert {(r["systemName"], r["externalId"]) for r in rows} == {("crm", "CRM-123")}


def test_merge_drops_external_id_when_survivor_already_has_that_system(client):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    _add_external_id(client, dealer_id, survivor["id"], "crm", "SURVIVOR-1")
    _add_external_id(client, dealer_id, duplicate["id"], "crm", "DUPLICATE-1")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    rows = client.get(f"/v1/customers/{survivor['id']}/external-ids", headers=_bearer(token)).json()["items"]
    # Survivor's own linkage wins; the duplicate's is dropped, not merged in.
    assert {(r["systemName"], r["externalId"]) for r in rows} == {("crm", "SURVIVOR-1")}


# --- audit -----------------------------------------------------------------------------


def test_merge_audit_log_reports_repoint_counts(client, engine):
    dealer_id = _create_dealer(client)
    survivor = _create_customer(client, dealer_id)
    duplicate = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    _create_transaction(engine, dealer_id, user, duplicate, vehicle)
    _add_phone(client, dealer_id, duplicate["id"], "+41791234599")

    _merge(client, dealer_id, duplicate["id"], survivor["id"])

    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    log = client.get(f"/v1/customers/{duplicate['id']}/audit-log", headers=_bearer(token))
    assert log.status_code == 200, log.text
    merge_event = next(item for item in log.json()["items"] if item["action"] == "merge")
    assert merge_event["after"]["transactionsRepointed"] == 1
    assert merge_event["after"]["phonesRepointed"] == 1
