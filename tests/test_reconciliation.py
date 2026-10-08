"""Nightly reconciliation (P-10) — the compensating control PR-2 ships
alongside dropping the nine cross-context foreign keys. Two things to
prove: (1) each context's job finds real orphans and leaves clean data
alone, including the nullable-column false-positive trap; (2) the delete
paths those FKs used to protect are still exactly what the audit found
before this PR — none, for the five FK-target entities.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.auth import AccessRole, create_access_token
from app.core.reconciliation import ReconciliationAlarm
from app.core.reconciliation_model import ReconciliationRun
from app.customer import reconciliation as customer_reconciliation
from app.customer.models.customer import Customer
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.public import reserve_for_contract
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item
from app.reconciliation_runner import MultiContextReconciliationAlarm, run_all
from app.sales import reconciliation as sales_reconciliation
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.valuation.models.valuation import ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation
from app.vehicle import reconciliation as vehicle_reconciliation
from app.vehicle.models.vehicle import CustodyEventType, VehicleCustodyEvent

VALID_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


# This file specifically exercises cross-context FK integrity (P-10), so a
# customer's group_id must point at a REAL dealer_group row — the
# uuid5-derived shadow value other test files use for mere token-to-token
# consistency would itself register as an orphan here. _create_dealer
# populates this cache with the real dealerGroupId every dealer it creates
# actually got; _token() consults it before falling back to the derived
# value for tenant_ids this file never created a dealer for.
_REAL_GROUP_IDS: dict[str, str] = {}


def _token(
    role: AccessRole | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    *,
    is_dealer_manager: bool = False,
) -> str:
    _tid = tenant_id or uuid.uuid4()
    real_group_id = _REAL_GROUP_IDS.get(str(_tid))
    return create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=_tid,
        group_id=uuid.UUID(real_group_id) if real_group_id else uuid.uuid5(uuid.NAMESPACE_OID, str(_tid)),
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
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(token))
    assert response.status_code == 201, response.text
    body = response.json()
    _REAL_GROUP_IDS[body["id"]] = body["dealerGroupId"]
    return body["id"]


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


def _create_transaction(db_session, dealer_id: str, user: dict, customer: dict, vehicle: dict) -> dict:
    """WP-8 PR-7 (ADR-050/S-D12): `transaction` writes are retired at the
    service layer — seeded directly via the ORM instead, on the SAME
    db_session the reconciliation job itself runs against.
    """

    transaction = Transaction(
        tenant_id=uuid.UUID(dealer_id),
        transaction_type=TransactionType.SALE,
        status=TransactionStatus.DRAFT,
        customer_id=uuid.UUID(customer["id"]),
        vehicle_id=uuid.UUID(vehicle["id"]),
        primary_user_id=uuid.UUID(user["id"]),
    )
    db_session.add(transaction)
    db_session.commit()
    db_session.refresh(transaction)
    return {"id": str(transaction.id)}


# --- clean data: every job finds nothing ------------------------------------------


def _create_vehicle_mdm(db_session) -> uuid.UUID:
    """WP-5 PR-2: a VehicleParty now references vehicle_mdm, not the legacy
    `vehicle` table — seeded via the service on the same db_session the
    reconciliation job runs against, same pattern as _create_transaction."""

    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm

    vehicle, _ = create_or_get_vehicle_mdm(db_session, vin=_random_vin(), catalogue_variant_id=None)
    db_session.commit()
    return vehicle.id


def test_customer_reconciliation_clean_data_finds_zero_orphans(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    vehicle_mdm_id = _create_vehicle_mdm(db_session)
    db_session.add(
        VehicleParty(
            vehicle_id=vehicle_mdm_id, customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.OWNER
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
    _create_transaction(db_session, dealer_id, user, customer, vehicle)
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
    assert alarm.orphans[0].check_label == "vehicle_party.vehicle_id -> vehicle_mdm.id"
    assert alarm.orphans[0].dangling_value == dangling_vehicle_id

    # The finding survives the raise — persisted before the alarm, not lost with it.
    db_session.expire_all()
    persisted = db_session.get(ReconciliationRun, alarm.run.id)
    assert persisted is not None
    assert persisted.orphans_found == 1


def test_customer_reconciliation_detects_orphaned_group_id(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    db_session.expire_all()

    row = db_session.get(Customer, uuid.UUID(customer["id"]))
    row.group_id = uuid.uuid4()
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        customer_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "customer.group_id -> dealer_group.id"


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
    assert exc_info.value.orphans[0].check_label == "vehicle_custody_event.partner_id -> dealership.id"


def test_sales_reconciliation_detects_orphaned_customer_id(client, db_session):
    dealer_id = _create_dealer(client)
    user = _create_user(client, dealer_id)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(db_session, dealer_id, user, customer, vehicle)
    db_session.expire_all()

    row = db_session.get(Transaction, uuid.UUID(txn["id"]))
    row.customer_id = uuid.uuid4()
    db_session.commit()
    db_session.expire_all()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "transaction.customer_id -> customer.id"


def test_sales_reconciliation_detects_a_purchase_replica_for_a_stock_item_that_does_not_exist(client, db_session):
    """KAN-100 — Sales' replica of Stock's purchase fact names a stock item
    by plain GUID (rule 2); nothing in the database stops it dangling."""

    dealer_id = _create_dealer(client)
    db_session.add(
        SalesStockItemPurchase(tenant_id=uuid.UUID(dealer_id), stock_item_id=uuid.uuid4(), source_event_id=uuid.uuid4())
    )
    db_session.commit()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    assert exc_info.value.orphans[0].check_label == "sales_stock_item_purchase.stock_item_id -> stock_item.id"


def _stock_item(db_session, dealer_id):
    return create_stock_item(
        db_session, tenant_id=uuid.UUID(dealer_id),
        data=StockItemCreate(vehicle_label="Seat Leon", condition=StockItemCondition.USED, vin=_random_vin()),
        actor_id=None,
    )


def _invoiceable_stock_item(db_session, dealer_id, *, hours_ago):
    """A stock item Stock holds as purchased, last touched `hours_ago`."""

    item = _stock_item(db_session, dealer_id)
    db_session.execute(
        update(StockItem)
        .where(StockItem.id == item.id)
        .values(is_invoiceable=True, updated_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago))
    )
    db_session.commit()
    return item


def test_sales_reconciliation_detects_a_purchase_sales_never_learned_of(client, db_session):
    """KAN-100 — e.g. a trade-in migrated by scripts/migrate_transaction_rows.py
    before it wrote the replica, or an event never published: Stock says
    purchased, Sales cannot invoice, and only reconciliation can tell."""

    dealer_id = _create_dealer(client)
    item = _invoiceable_stock_item(db_session, dealer_id, hours_ago=2)

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    orphan = exc_info.value.orphans[0]
    assert orphan.check_label == "stock item purchased in Stock with no purchase replica in its dealership"
    assert (orphan.source_table, orphan.source_row_id) == ("stock_item", item.id)


def test_sales_reconciliation_leaves_a_purchase_still_within_outbox_lag_alone(client, db_session):
    dealer_id = _create_dealer(client)
    _invoiceable_stock_item(db_session, dealer_id, hours_ago=0)

    assert sales_reconciliation.run(db_session).orphans_found == 0


def test_sales_reconciliation_accepts_a_mirrored_purchase(client, db_session):
    dealer_id = _create_dealer(client)
    item = _invoiceable_stock_item(db_session, dealer_id, hours_ago=2)
    db_session.add(SalesStockItemPurchase(tenant_id=uuid.UUID(dealer_id), stock_item_id=item.id, source_event_id=None))
    db_session.commit()

    assert sales_reconciliation.run(db_session).orphans_found == 0


def _manual_contract(db_session, dealer_id, *, signed_hours_ago, stock_item_id=None):
    contract = SalesContract(
        tenant_id=uuid.UUID(dealer_id), contract_number=f"C-{uuid.uuid4().hex[:6]}", vehicle_source="manual",
        vehicle_label="Volkswagen ID.4 Pro", status=ContractStatus.CONFIRMED, stock_item_id=stock_item_id,
        signed_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=signed_hours_ago),
    )
    db_session.add(contract)
    db_session.commit()
    return contract


def test_sales_reconciliation_detects_a_manual_configuration_never_linked(client, db_session):
    """KAN-144 — the link arrives on inventory.stock_item.added within outbox
    lag; a confirmed manual contract still unlinked after that can never be
    invoiced (a lost event, or one confirmed before KAN-144 — KAN-159)."""

    dealer_id = _create_dealer(client)
    contract = _manual_contract(db_session, dealer_id, signed_hours_ago=2)

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    orphan = exc_info.value.orphans[0]
    assert orphan.check_label == "confirmed manual configuration with no pipeline stock item"
    assert (orphan.source_table, orphan.source_row_id, orphan.dangling_value) == ("sales_contract", contract.id, contract.id)


def test_sales_reconciliation_leaves_a_fresh_or_linked_manual_contract_alone(client, db_session):
    dealer_id = _create_dealer(client)
    _manual_contract(db_session, dealer_id, signed_hours_ago=0)
    item = _invoiceable_stock_item(db_session, dealer_id, hours_ago=0)
    _manual_contract(db_session, dealer_id, signed_hours_ago=2, stock_item_id=item.id)

    assert sales_reconciliation.run(db_session).orphans_found == 0


def test_sales_reconciliation_detects_a_purchase_recorded_for_another_dealership(client, db_session):
    """KAN-145 — SalesContract.is_invoiceable matches the replica on tenant
    as well as item, so a replica filed under another dealership leaves the
    car un-invoiceable where it was bought, and claims a purchase the other
    dealership never made. Both rows are findings."""

    dealer_id = _create_dealer(client)
    other_dealer_id = _create_dealer(client)
    item = _invoiceable_stock_item(db_session, dealer_id, hours_ago=2)
    replica = SalesStockItemPurchase(tenant_id=uuid.UUID(other_dealer_id), stock_item_id=item.id, source_event_id=None)
    db_session.add(replica)
    db_session.commit()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert sorted((o.check_label, o.source_table, o.source_row_id) for o in exc_info.value.orphans) == [
        ("purchase replica whose stock item is not purchased in Stock for its dealership",
         "sales_stock_item_purchase", replica.id),
        ("stock item purchased in Stock with no purchase replica in its dealership", "stock_item", item.id),
    ]


def test_sales_reconciliation_detects_a_purchase_stock_never_recorded(client, db_session):
    """KAN-145 — a replica (written by a script, say) for a car Stock does not
    hold as purchased: Sales would let it be invoiced before the dealership
    has bought it."""

    dealer_id = _create_dealer(client)
    item = _stock_item(db_session, dealer_id)
    replica = SalesStockItemPurchase(tenant_id=uuid.UUID(dealer_id), stock_item_id=item.id, source_event_id=None)
    db_session.add(replica)
    db_session.commit()

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert exc_info.value.run.orphans_found == 1
    orphan = exc_info.value.orphans[0]
    assert orphan.check_label == "purchase replica whose stock item is not purchased in Stock for its dealership"
    assert (orphan.source_table, orphan.source_row_id, orphan.dangling_value) == (
        "sales_stock_item_purchase", replica.id, replica.id,
    )


def _offer(db_session, dealer_id, **columns):
    offer = SalesOffer(tenant_id=uuid.UUID(dealer_id), offer_number=f"O-{uuid.uuid4().hex[:6]}")
    for name, value in columns.items():
        setattr(offer, name, value)
    db_session.add(offer)
    db_session.commit()
    return offer


def _contract(db_session, dealer_id, **columns):
    contract = SalesContract(tenant_id=uuid.UUID(dealer_id), contract_number=f"C-{uuid.uuid4().hex[:6]}")
    for name, value in columns.items():
        setattr(contract, name, value)
    db_session.add(contract)
    db_session.commit()
    return contract


@pytest.mark.parametrize(
    ("seed", "column", "label", "extra"),
    [
        (_offer, "tenant_id", "sales_offer.tenant_id -> dealership.id", {}),
        (_offer, "created_by", "sales_offer.created_by -> user.id", {}),
        (_offer, "updated_by", "sales_offer.updated_by -> user.id", {}),
        (_offer, "customer_id", "sales_offer.customer_id -> customer.id", {}),
        (_offer, "stock_item_id", "sales_offer.stock_item_id -> stock_item.id", {}),
        (_offer, "trade_in_vehicle_id", "sales_offer.trade_in_vehicle_id -> vehicle_mdm.id", {}),
        (_offer, "trade_in_valuation_id", "sales_offer.trade_in_valuation_id -> valuation.id", {}),
        (_contract, "tenant_id", "sales_contract.tenant_id -> dealership.id", {}),
        (_contract, "created_by", "sales_contract.created_by -> user.id", {}),
        (_contract, "updated_by", "sales_contract.updated_by -> user.id", {}),
        (_contract, "customer_id", "sales_contract.customer_id -> customer.id", {}),
        (_contract, "stock_item_id", "sales_contract.stock_item_id -> stock_item.id", {}),
        (_contract, "trade_in_vehicle_id", "sales_contract.trade_in_vehicle_id -> vehicle_mdm.id", {}),
        (_contract, "trade_in_valuation_id", "sales_contract.trade_in_valuation_id -> valuation.id", {}),
        (
            _contract, "reservation_id", "sales_contract.reservation_id -> stock_item.active_reservation_id",
            {"status": ContractStatus.CONFIRMED, "signed_at": dt.datetime.now(dt.UTC)},
        ),
    ],
)
def test_sales_reconciliation_detects_each_dangling_offer_and_contract_reference(
    client, db_session, seed, column, label, extra
):
    """KAN-145 — every cross-context id on sales_offer and sales_contract is
    a plain GUID (rule 2); only reconciliation can tell when one dangles."""

    dealer_id = _create_dealer(client)
    dangling = uuid.uuid4()
    row = seed(db_session, dealer_id, **{column: dangling}, **extra)

    with pytest.raises(ReconciliationAlarm) as exc_info:
        sales_reconciliation.run(db_session)

    assert [(o.check_label, o.source_row_id, o.dangling_value) for o in exc_info.value.orphans] == [
        (label, row.id, dangling)
    ]


def test_sales_reconciliation_accepts_offers_and_contracts_whose_references_resolve(client, db_session):
    """The clean side of KAN-145: real references, absent (null) ones, a
    confirmed contract whose car is still held for it, and a cancelled
    contract that keeps the id of the hold it released."""

    dealer_id = _create_dealer(client)
    user_id = uuid.UUID(_create_user(client, dealer_id)["id"])
    customer_id = uuid.UUID(_create_customer(client, dealer_id)["id"])
    trade_in_vehicle_id = _create_vehicle_mdm(db_session)
    valuation = create_valuation(
        db_session, tenant_id=uuid.UUID(dealer_id), group_id=uuid.UUID(_REAL_GROUP_IDS[dealer_id]), actor_id=None,
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal("2500.00")),
    )
    db_session.commit()
    held_item = _stock_item(db_session, dealer_id)
    released_item = _stock_item(db_session, dealer_id)
    references = {
        "created_by": user_id, "updated_by": user_id, "customer_id": customer_id,
        "trade_in_vehicle_id": trade_in_vehicle_id, "trade_in_valuation_id": valuation.id,
    }

    _offer(db_session, dealer_id)
    _offer(db_session, dealer_id, stock_item_id=held_item.id, **references)
    confirmed = _contract(
        db_session, dealer_id, status=ContractStatus.CONFIRMED, signed_at=dt.datetime.now(dt.UTC),
        vehicle_source="stock", stock_item_id=held_item.id, **references,
    )
    held = reserve_for_contract(
        db_session, tenant_id=uuid.UUID(dealer_id), stock_item_id=held_item.id, contract_id=confirmed.id,
        idempotency_key=f"test:{confirmed.id}",
    )
    confirmed.reservation_id = uuid.UUID(held["reservationId"])
    db_session.commit()
    _contract(
        db_session, dealer_id, status=ContractStatus.CANCELLED, vehicle_source="stock",
        stock_item_id=released_item.id, reservation_id=uuid.uuid4(),
    )

    run = sales_reconciliation.run(db_session)

    assert run.orphans_found == 0


# --- top-level runner: every context runs even when an earlier one alarms --------


def test_run_all_runs_every_context_and_aggregates_alarms(client, db_session):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    user = _create_user(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    txn = _create_transaction(db_session, dealer_id, user, customer, vehicle)
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
        "/v1/dealerships/{dealer_id}",
        "/v1/dealerships/{dealer_id}/users/{user_id}",
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
        "/v1/customers/{customer_id}/addresses/{address_id}",
        "/v1/customers/{customer_id}/external-ids/{external_id_row_id}",
        "/v1/customers/{customer_id}/vehicles/{party_id}",
        "/v1/me/preferences/{scope}",
        # WP-7 PR-8: stock_item_media is inventory's own new child row
        # (ADR-062) — a real hard delete this time (removing a photo from
        # a listing genuinely removes the row, unlike the closed-not-
        # deleted rows below), never a target of any dropped FK.
        "/v1/inventory/stock-items/{stock_item_id}/media/{media_id}",
        # WP-5 PR-9: another genuine child row (vehicle_accessory), not a
        # target of any dropped FK. Same shape as the vehicles/{party_id}
        # row above it — the DELETE verb closes (sets valid_to), it never
        # actually deletes (FR-V-13).
        "/v1/vehicle-mdm/{vehicle_id}/accessories/{accessory_id}",
        # WP-6 PR-1: integration_connection and integration_secret_ref are
        # brand-new tables, never a target of any of the nine dropped FKs
        # this test's own docstring is about. A real hard delete here is
        # deliberate (Integrations & API Credentials v0.1, rule 10:
        # "deleting requires confirmation and is audit-logged") — deleting
        # a connection genuinely removes it and every one of its secret
        # refs from Infisical too, unlike the closed-not-deleted rows above.
        "/v1/integrations/connections/{connection_id}",
        "/v1/integrations/connections/{connection_id}/secrets/{slot}",
    }
