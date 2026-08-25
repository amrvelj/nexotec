"""Nightly reconciliation (P-10) — the compensating control PR-2 ships
alongside dropping the nine cross-context foreign keys. Two things to
prove: (1) each context's job finds real orphans and leaves clean data
alone, including the nullable-column false-positive trap; (2) the delete
paths those FKs used to protect are still exactly what the audit found
before this PR — none, for the five FK-target entities.
"""

import datetime as dt
import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.reconciliation import ReconciliationAlarm
from app.core.reconciliation_model import ReconciliationRun
from app.customer import reconciliation as customer_reconciliation
from app.customer.models.customer import Customer
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.reconciliation_runner import MultiContextReconciliationAlarm, run_all
from app.sales import reconciliation as sales_reconciliation
from app.sales.models.transaction import Transaction
from app.vehicle import reconciliation as vehicle_reconciliation
from app.vehicle.models.vehicle import CustodyEventType, VehicleCustodyEvent

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
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_dealer(client) -> str:
    token = _token(AccessRole.PLATFORM_ADMIN)
    payload = {
        "legalName": "Garage Musterbetrieb AG",
        "dealerLicenseNumber": "ZH-12345",
        "licenseState": "ZH",
        "franchiseType": "independent",
        "address": VALID_ADDRESS,
        "phone": "+41441234567",
        "taxId": "CHE-123.456.789",
    }
    response = client.post("/v1/dealers", json=payload, headers=_bearer(token))
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
        "authIdentityId": "stub-sub-1",
    }
    payload.update(overrides)
    response = client.post(f"/v1/dealers/{dealer_id}/users", json=payload, headers=_bearer(admin_token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_customer(client, dealer_id: str, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "private", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
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


def _create_transaction(client, dealer_id: str, user: dict, customer: dict, vehicle: dict, **overrides) -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    payload = {
        "transactionType": "sale",
        "customerId": customer["id"],
        "vehicleId": vehicle["id"],
        "primaryUserId": user["id"],
    }
    payload.update(overrides)
    response = client.post("/v1/transactions", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- clean data: every job finds nothing ------------------------------------------


def test_customer_reconciliation_clean_data_finds_zero_orphans(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    db_session.add(
        VehicleParty(
            vehicle_id=uuid.UUID(vehicle["id"]), customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.OWNER
        )
    )
    db_session.commit()
    db_session.expire_all()

    run = customer_reconciliation.run(db_session)

    assert run.orphans_found == 0
    assert run.checks_run == len(customer_reconciliation.CHECKS)
    assert run.finished_at is not None


def test_vehicle_reconciliation_clean_data_and_null_custodian_finds_zero_orphans(client, db_session):
    """current_custodian_partner_id defaults to NULL until a custody event
    assigns it — that must never be flagged as an orphan, since NULL means
    "no reference yet", not "dangling reference".
    """

    dealer_id = _create_dealer(client)
    _create_vehicle(client, dealer_id)  # current_custodian_partner_id stays NULL
    db_session.expire_all()

    run = vehicle_reconciliation.run(db_session)

    assert run.orphans_found == 0
    assert run.checks_run == len(vehicle_reconciliation.CHECKS)


def test_sales_reconciliation_clean_data_finds_zero_orphans(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    _create_transaction(client, dealer_id, user, customer, vehicle)
    db_session.expire_all()

    run = sales_reconciliation.run(db_session)

    assert run.orphans_found == 0
    assert run.checks_run == len(sales_reconciliation.CHECKS)


# --- seeded orphans: each job catches its own kind of dangling reference ----------


def test_customer_reconciliation_detects_orphaned_vehicle_party(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    dangling_vehicle_id = uuid.uuid4()
    db_session.add(
        VehicleParty(vehicle_id=dangling_vehicle_id, customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.OWNER)
    )
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        customer_reconciliation.run(db_session)

    alarm = exc_info.value
    assert alarm.run.orphans_found == 1
    assert alarm.orphans[0].check_label == "vehicle_party.vehicle_id -> vehicle.id"
    assert alarm.orphans[0].dangling_value == dangling_vehicle_id

    # The finding survives the raise — persisted before the alarm, not lost with it.
    db_session.expire_all()
    persisted = db_session.get(ReconciliationRun, alarm.run.id)
    assert persisted is not None
    assert persisted.orphans_found == 1


def test_customer_reconciliation_detects_orphaned_tenant_id(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    db_session.expire_all()

    row = db_session.get(Customer, uuid.UUID(customer["id"]))
    row.tenant_id = uuid.uuid4()
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        customer_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "customer.tenant_id -> dealer.id"


def test_vehicle_reconciliation_detects_orphaned_custody_event_partner(client, db_session):
    dealer_id = _create_dealer(client)
    vehicle = _create_vehicle(client, dealer_id)
    db_session.add(
        VehicleCustodyEvent(
            vehicle_id=uuid.UUID(vehicle["id"]),
            partner_id=uuid.uuid4(),
            event_type=CustodyEventType.ACQUIRED,
            event_date=dt.datetime.now(dt.UTC),
        )
    )
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        vehicle_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "vehicle_custody_event.partner_id -> dealer.id"


def test_sales_reconciliation_detects_orphaned_customer_id(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(client, dealer_id, user, customer, vehicle)
    db_session.expire_all()

    row = db_session.get(Transaction, uuid.UUID(txn["id"]))
    row.customer_id = uuid.uuid4()
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "transaction.customer_id -> customer.id"


# --- top-level runner: every context runs even when an earlier one alarms --------


def test_run_all_runs_every_context_and_aggregates_alarms(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(client, dealer_id, user, customer, vehicle)
    db_session.expire_all()

    # Orphan customer (via vehicle_party) and sales (via transaction), leave
    # vehicle clean — proves run_all doesn't stop at the first alarm.
    db_session.add(VehicleParty(vehicle_id=uuid.uuid4(), customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.OWNER))
    txn_row = db_session.get(Transaction, uuid.UUID(txn["id"]))
    txn_row.vehicle_id = uuid.uuid4()
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(MultiContextReconciliationAlarm) as exc_info:
        run_all(db_session)

    contexts_with_alarms = {alarm.run.context for alarm in exc_info.value.alarms}
    assert contexts_with_alarms == {"customer", "sales"}


# --- delete-path regression: the audit's findings must stay true -----------------


def test_no_hard_delete_endpoint_exists_for_fk_target_entities(client):
    """Customer, Vehicle, Dealer, Transaction and User are the five tables
    the dropped FKs pointed at. The delete-path audit (PR-2) found no
    DELETE endpoint for any of them — status/lifecycle fields are the only
    way to retire one. If this ever changes, reconciliation and this
    contract both need a conscious update, not a silent one.
    """

    schema = client.app.openapi()
    delete_paths = {path for path, methods in schema["paths"].items() if "delete" in methods}

    for forbidden in (
        "/v1/dealers/{dealer_id}",
        "/v1/dealers/{dealer_id}/users/{user_id}",
        "/v1/customers/{customer_id}",
        "/v1/vehicles/{vehicle_id}",
        "/v1/transactions/{transaction_id}",
    ):
        assert forbidden not in delete_paths, f"unexpected hard-delete endpoint: {forbidden}"

    # The only DELETE endpoints that do exist are for genuine child rows
    # that were never a target of any of the nine dropped FKs.
    assert delete_paths == {
        "/v1/customers/{customer_id}/phones/{phone_id}",
        "/v1/customers/{customer_id}/emails/{email_id}",
        "/v1/customers/{customer_id}/external-ids/{external_id_row_id}",
        "/v1/customers/{customer_id}/vehicles/{party_id}",
        "/v1/me/preferences/{scope}",
    }
