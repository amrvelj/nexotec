"""KAN-26 (WP-8 PR-7, ADR-050): migrating the retired `transaction` table's
rows into sales_contract (+ a synthesised offer and a retroactive
StockItem) or, for trade-ins, an unconditional rejection — see
scripts/migrate_transaction_rows.py's own module docstring for why.
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.customer.models.customer import Customer, CustomerType, Language
from app.inventory.models.stock_item import StockItem
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition, VehicleStatus
from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm
from scripts.migrate_transaction_rows import run_migration

TENANT_ID = uuid.uuid4()


def _customer(db_session) -> Customer:
    customer = Customer(
        group_id=uuid.uuid4(), customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def _legacy_vehicle(db_session, vin="ZAR94000007123456", condition=VehicleCondition.USED) -> LegacyVehicle:
    vehicle = LegacyVehicle(
        vin=vin, make="Alfa Romeo", model="Giulietta", model_year=2020, trim="1.4 TB Progression",
        condition=condition, status=VehicleStatus.IN_STOCK,
    )
    db_session.add(vehicle)
    db_session.flush()
    return vehicle


def _migrated_vehicle_mdm(db_session, legacy: LegacyVehicle):
    mdm, _created = create_or_get_vehicle_mdm(db_session, vin=legacy.vin, catalogue_variant_id=None)
    mdm.migrated_from_legacy_vehicle_id = legacy.id
    db_session.flush()
    return mdm


def _sale_transaction(db_session, *, customer_id, vehicle_id, amount=Decimal("35000.00"), status=TransactionStatus.COMPLETED):
    txn = Transaction(
        tenant_id=TENANT_ID, transaction_type=TransactionType.SALE, status=status,
        customer_id=customer_id, vehicle_id=vehicle_id, primary_user_id=uuid.uuid4(),
        amount=amount, transaction_date=dt.datetime(2024, 3, 15, tzinfo=dt.UTC),
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def test_dry_run_writes_nothing(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=False)

    assert report.committed is False
    assert [o.outcome for o in report.outcomes] == ["migrated"]
    assert db_session.query(SalesContract).filter_by(legacy_transaction_id=txn.id).one_or_none() is None
    assert db_session.query(StockItem).count() == 0


def test_commit_migrates_a_completed_sale_with_a_real_stock_link(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    txn = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("35000.00"))
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.committed is True
    assert [o.outcome for o in report.outcomes] == ["migrated"]

    contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=txn.id).one()
    assert contract.status == ContractStatus.CONFIRMED
    assert contract.customer_id == customer.id
    assert contract.gross_price == Decimal("35000.00")
    assert contract.vehicle_source == "stock"
    assert contract.offer_id is not None

    # The structural, joinable link the product owner asked for — not text.
    stock_item = db_session.get(StockItem, contract.stock_item_id)
    assert stock_item is not None
    assert stock_item.vehicle_id == mdm.id
    assert stock_item.left_stock_at is not None  # ADR-054: sold = left stock, not a 4th lifecycle value

    offer = db_session.get(SalesOffer, contract.offer_id)
    assert offer is not None
    assert offer.stock_item_id == stock_item.id
    assert offer.vehicle_snapshot_frozen_at is not None  # the real freeze_vehicle_snapshot ran, not a stand-in


def test_rerunning_after_commit_is_idempotent(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    run_migration(db_session, commit=True)
    second = run_migration(db_session, commit=True)

    assert [o.outcome for o in second.outcomes] == ["already_migrated"]
    assert db_session.query(SalesContract).count() == 1
    assert db_session.query(StockItem).count() == 1


def test_draft_and_cancelled_transactions_are_never_migrated(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, status=TransactionStatus.DRAFT)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, status=TransactionStatus.CANCELLED)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["not_completed", "not_completed"]
    assert db_session.query(SalesContract).count() == 0


def test_a_vehicle_that_was_never_migrated_to_vehicle_mdm_is_reported_never_guessed(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    # Deliberately no create_or_get_vehicle_mdm / migrated_from_legacy_vehicle_id.
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["vehicle_unresolved"]
    assert db_session.query(SalesContract).count() == 0


def test_an_unresolvable_customer_is_reported_never_guessed(db_session):
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _sale_transaction(db_session, customer_id=uuid.uuid4(), vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["customer_unresolved"]
    assert db_session.query(SalesContract).count() == 0


def test_certified_pre_owned_condition_is_approximated_and_flagged(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session, condition=VehicleCondition.CERTIFIED_PRE_OWNED)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.outcomes[0].outcome == "migrated"
    assert "certified_pre_owned" in report.outcomes[0].notes
    contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=txn.id).one()
    stock_item = db_session.get(StockItem, contract.stock_item_id)
    assert stock_item.condition.value == "used"


def test_every_trade_in_row_is_rejected_unconditionally(db_session):
    """No supplier_is_vat_registered or condition source exists anywhere
    on the legacy transaction row — per the product owner's own
    instruction, a missing required input is a bug to file, never a
    guessed default.
    """

    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = Transaction(
        tenant_id=TENANT_ID, transaction_type=TransactionType.TRADE_IN, status=TransactionStatus.COMPLETED,
        customer_id=customer.id, vehicle_id=legacy.id, primary_user_id=uuid.uuid4(),
        amount=Decimal("12000.00"), transaction_date=dt.datetime(2024, 3, 15, tzinfo=dt.UTC),
    )
    db_session.add(txn)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.outcomes[0].outcome == "rejected_trade_in"
    assert "supplier_is_vat_registered" in report.outcomes[0].notes
    assert db_session.query(SalesContract).count() == 0


def test_transaction_table_itself_is_never_written_to(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    original_version = txn.version

    run_migration(db_session, commit=True)

    db_session.refresh(txn)
    assert txn.version == original_version
    assert txn.status == TransactionStatus.COMPLETED
