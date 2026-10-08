"""KAN-26 (WP-8 PR-7, ADR-050): migrating the retired `transaction` table's
rows into sales_contract (+ a synthesised offer) for `sale` rows, or a
StockItem carrying the acquisition for `trade_in` rows — see
scripts/migrate_transaction_rows.py's own module docstring for why the
trade-in path is no longer an unconditional rejection, and for how the
two row types share a stock item: a resale closes out the trade-in's item,
a car traded back in after a sale gets a second item (KAN-256), and a car
live Stock holds is never touched.
"""

import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal

from app.customer.models.customer import Customer, CustomerType, Language
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition, VehicleStatus
from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm
from scripts.migrate_transaction_rows import run_migration

TENANT_ID = uuid.uuid4()


def _dealership(db_session, *, vat_rate=Decimal("8.10")) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=TENANT_ID,
        dealer_group_id=group.id,
        legal_name="Garage AG",
        dealer_license_number="ZH-1",
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        tax_id="CHE-123.456.789",
        vat_rate=vat_rate,
    )
    db_session.add(dealership)
    db_session.flush()
    return dealership


def _customer(db_session, *, vat_registered=False) -> Customer:
    customer = Customer(
        group_id=uuid.uuid4(), customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace", vat_registered=vat_registered,
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


def _sale_transaction(
    db_session, *, customer_id, vehicle_id, amount=Decimal("35000.00"), status=TransactionStatus.COMPLETED,
    transaction_date=dt.datetime(2024, 3, 15, tzinfo=dt.UTC),
):
    txn = Transaction(
        tenant_id=TENANT_ID, transaction_type=TransactionType.SALE, status=status,
        customer_id=customer_id, vehicle_id=vehicle_id, primary_user_id=uuid.uuid4(),
        amount=amount, transaction_date=transaction_date,
    )
    db_session.add(txn)
    db_session.flush()
    return txn


def _trade_in_transaction(
    db_session, *, customer_id, vehicle_id, amount=Decimal("12000.00"), status=TransactionStatus.COMPLETED,
    transaction_date=dt.datetime(2024, 1, 10, tzinfo=dt.UTC),
):
    txn = Transaction(
        tenant_id=TENANT_ID, transaction_type=TransactionType.TRADE_IN, status=status,
        customer_id=customer_id, vehicle_id=vehicle_id, primary_user_id=uuid.uuid4(),
        amount=amount, transaction_date=transaction_date,
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
    # KAN-100: no purchase was ever booked for a car only ever sold, so the
    # contract derives not-invoiceable from Stock's fact. (That a migrated,
    # CONFIRMED legacy sale could still be handed to invoicing when its car
    # came in as a legacy trade-in is a separate, older gap.)
    assert contract.is_invoiceable is False


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


def test_a_sale_with_missing_amount_is_reported_never_defaulted_to_zero(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=None)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["amount_missing"]
    assert db_session.query(SalesContract).count() == 0
    assert db_session.query(StockItem).count() == 0


def test_a_sale_with_missing_transaction_date_is_reported_never_guessed(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, transaction_date=None)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["transaction_date_missing"]
    assert db_session.query(SalesContract).count() == 0


def test_dry_run_trade_in_writes_nothing(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=False)

    assert report.committed is False
    assert [o.outcome for o in report.outcomes] == ["migrated"]
    assert db_session.query(StockItem).count() == 0
    assert db_session.query(SalesStockItemPurchase).count() == 0


def test_a_trade_in_migrates_as_a_stock_acquisition_from_a_private_individual(db_session):
    _dealership(db_session, vat_rate=Decimal("8.10"))
    customer = _customer(db_session, vat_registered=False)
    legacy = _legacy_vehicle(db_session, condition=VehicleCondition.USED)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    txn = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("12000.00"))
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["migrated"]
    assert db_session.query(SalesContract).count() == 0  # a trade-in never creates a contract by itself

    item = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{txn.id}").one()
    assert item.vehicle_id == mdm.id
    assert item.vin == mdm.vin
    assert item.lifecycle_status.value == "in_stock"
    assert item.left_stock_at is None  # unlike a sale, we don't know if/when it later left stock
    assert item.condition.value == "used"
    assert item.supplier_name == "Ada Lovelace"
    assert item.supplier_is_vat_registered is False
    assert item.purchase_price == Decimal("12000.00")
    assert item.purchase_date == txn.transaction_date.date()
    assert item.landed_cost is None
    # Art. 28a MWSTG: private seller -> notional input tax IS applicable.
    # Pinned to the same ROUND_HALF_UP the production formula uses (not the
    # ambient decimal-context default), so this test can't silently agree
    # with a wrong rounding mode on a future rounding-tie fixture value.
    assert item.notional_input_tax_applicable is True
    assert item.notional_input_tax_rate == Decimal("8.10")
    assert item.notional_input_tax_amount == (Decimal("12000.00") * Decimal("8.10") / Decimal("108.10")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    assert item.is_invoiceable is True
    assert report.outcomes[0].new_stock_item_id == item.id
    # KAN-100: the script publishes no inventory.stock_item.purchased, so it
    # records the purchase in Sales' replica itself — or no later contract
    # on this car could ever be invoiced.
    replica = db_session.query(SalesStockItemPurchase).one()
    assert (replica.tenant_id, replica.stock_item_id, replica.source_event_id) == (item.tenant_id, item.id, None)
    assert replica.stock_item_label == item.stock_number  # KAN-150, rule 2
    assert replica.stock_item_denorm_refreshed_at is not None


def test_a_trade_in_from_a_vat_registered_business_has_no_notional_input_tax(db_session):
    _dealership(db_session)
    customer = _customer(db_session, vat_registered=True)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    run_migration(db_session, commit=True)

    item = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{txn.id}").one()
    assert item.supplier_is_vat_registered is True
    assert item.notional_input_tax_applicable is False
    assert item.notional_input_tax_rate is None
    assert item.notional_input_tax_amount is None


def test_a_trade_in_with_a_genuinely_zero_purchase_price_is_not_conflated_with_missing(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("0.00"))
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["migrated"]  # zero is a real value, not "missing"
    item = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{txn.id}").one()
    assert item.purchase_price == Decimal("0.00")
    assert item.notional_input_tax_amount == Decimal("0.00")


def test_trade_in_rerun_is_idempotent(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    run_migration(db_session, commit=True)
    second = run_migration(db_session, commit=True)

    assert [o.outcome for o in second.outcomes] == ["already_migrated"]
    assert db_session.query(StockItem).count() == 1
    assert db_session.query(SalesStockItemPurchase).count() == 1


def test_a_trade_in_with_an_unresolvable_vehicle_is_reported_never_guessed(db_session):
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    # Deliberately no create_or_get_vehicle_mdm / migrated_from_legacy_vehicle_id.
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["vehicle_unresolved"]
    assert db_session.query(StockItem).count() == 0


def test_a_trade_in_with_an_unresolvable_customer_is_reported_never_guessed(db_session):
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=uuid.uuid4(), vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["customer_unresolved"]
    assert db_session.query(StockItem).count() == 0


def test_a_trade_in_with_no_dealership_is_reported_never_guessed(db_session):
    """A trade-in (unlike a sale) needs the dealer's own vat_rate to
    compute the notional input tax — Transaction.tenant_id carries no
    DB-level FK to Dealership (ADR-015), so old legacy data can point at
    a tenant with no surviving Dealership row.
    """

    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    # Deliberately no _dealership(db_session) call.
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["dealership_unresolved"]
    assert db_session.query(StockItem).count() == 0


def test_a_trade_in_with_missing_amount_is_reported_never_defaulted_to_zero(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=None)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["amount_missing"]
    assert db_session.query(StockItem).count() == 0


def test_a_trade_in_with_missing_transaction_date_is_reported_never_guessed(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id, transaction_date=None)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["transaction_date_missing"]
    assert db_session.query(StockItem).count() == 0


def test_certified_pre_owned_trade_in_condition_is_approximated_and_flagged(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session, condition=VehicleCondition.CERTIFIED_PRE_OWNED)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.outcomes[0].outcome == "migrated"
    assert "certified_pre_owned" in report.outcomes[0].notes
    item = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{txn.id}").one()
    assert item.condition.value == "used"


def test_certified_pre_owned_condition_combined_with_a_vat_registered_customer_trade_in(db_session):
    """The two independent per-row facets (a lossy condition mapping, and
    a VAT-registered supplier) fire on the SAME row — confirms they don't
    interact badly (e.g. one clobbering the other's field writes).
    """

    _dealership(db_session)
    customer = _customer(db_session, vat_registered=True)
    legacy = _legacy_vehicle(db_session, condition=VehicleCondition.CERTIFIED_PRE_OWNED)
    _migrated_vehicle_mdm(db_session, legacy)
    txn = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.outcomes[0].outcome == "migrated"
    assert "certified_pre_owned" in report.outcomes[0].notes
    item = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{txn.id}").one()
    assert item.condition.value == "used"
    assert item.supplier_is_vat_registered is True
    assert item.notional_input_tax_applicable is False


def test_a_vehicle_traded_in_and_later_resold_ends_up_as_one_stock_item_with_both_facts(db_session):
    """The core interaction the module docstring calls out: a vehicle
    traded in and later resold — two separate completed `transaction` rows
    — is one stay in stock, so both rows share ONE stock item: the
    trade-in's acquisition facts and the sale's own disposal facts
    (left_stock_at, resale price) and its contract all land on/reference
    the SAME row, rather than the resale being silently walked away from.
    """

    _dealership(db_session)
    trade_in_customer = _customer(db_session)
    sale_customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    trade_in_txn = _trade_in_transaction(
        db_session, customer_id=trade_in_customer.id, vehicle_id=legacy.id, amount=Decimal("12000.00"),
        transaction_date=dt.datetime(2024, 1, 10, tzinfo=dt.UTC),
    )
    sale_txn = _sale_transaction(
        db_session, customer_id=sale_customer.id, vehicle_id=legacy.id, amount=Decimal("15000.00"),
        transaction_date=dt.datetime(2024, 6, 20, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[trade_in_txn.id].outcome == "migrated"
    assert outcomes_by_txn[sale_txn.id].outcome == "migrated"
    assert db_session.query(StockItem).count() == 1  # one stay in stock, so one item for both rows

    item = db_session.query(StockItem).one()
    assert item.purchase_price == Decimal("12000.00")  # the trade-in's own acquisition fact, untouched by the sale
    assert item.supplier_is_vat_registered is False
    assert item.left_stock_at is not None  # the resale closed it out
    assert item.effective_price == Decimal("15000.00")  # the resale's own disposal price
    assert item.is_invoiceable is True

    contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=sale_txn.id).one()
    assert contract.stock_item_id == item.id
    assert contract.gross_price == Decimal("15000.00")
    assert contract.is_invoiceable is True  # KAN-100: the trade-in booked the purchase


def test_processing_order_is_driven_by_transaction_date_not_insertion_order(db_session):
    """The interaction above relies on rows being processed by
    transaction_date, not by the order they were inserted/returned by an
    unordered SELECT. Insert the SALE row first in code, with the
    TRADE-IN's date earlier, and confirm the trade-in is still resolved
    first (the acquisition fields survive on the shared item) — proving
    the ORDER BY drives this, not coincidental insertion order.
    """

    _dealership(db_session)
    sale_customer = _customer(db_session)
    trade_in_customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    # Sale inserted FIRST in code, but dated LATER.
    sale_txn = _sale_transaction(
        db_session, customer_id=sale_customer.id, vehicle_id=legacy.id, amount=Decimal("15000.00"),
        transaction_date=dt.datetime(2024, 6, 20, tzinfo=dt.UTC),
    )
    trade_in_txn = _trade_in_transaction(
        db_session, customer_id=trade_in_customer.id, vehicle_id=legacy.id, amount=Decimal("12000.00"),
        transaction_date=dt.datetime(2024, 1, 10, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[trade_in_txn.id].outcome == "migrated"
    assert outcomes_by_txn[sale_txn.id].outcome == "migrated"
    item = db_session.query(StockItem).one()
    assert item.purchase_price == Decimal("12000.00")  # the trade-in's acquisition data made it onto the item
    assert item.left_stock_at is not None


def test_a_vehicle_sold_then_later_reacquired_as_a_trade_in_gets_a_second_stock_item(db_session):
    """KAN-256 (Anto, 2026-10-08): a car sold once and later traded back in
    to the SAME dealer becomes a second stock item, as live Stock does since
    KAN-111. The sold item stays as history and is never reopened: its
    left_stock_at, its sale price and its contract are untouched, and the
    reacquisition's purchase facts land on the new item only.
    """

    _dealership(db_session)
    first_owner = _customer(db_session)
    second_owner = _customer(db_session, vat_registered=True)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    first_sale_txn = _sale_transaction(
        db_session, customer_id=first_owner.id, vehicle_id=legacy.id, amount=Decimal("20000.00"),
        transaction_date=dt.datetime(2023, 1, 5, tzinfo=dt.UTC),
    )
    reacquisition_txn = _trade_in_transaction(
        db_session, customer_id=second_owner.id, vehicle_id=legacy.id, amount=Decimal("9000.00"),
        transaction_date=dt.datetime(2024, 8, 1, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[first_sale_txn.id].outcome == "migrated"
    assert outcomes_by_txn[reacquisition_txn.id].outcome == "migrated"
    assert db_session.query(StockItem).filter_by(vin=mdm.vin).count() == 2

    first_contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=first_sale_txn.id).one()
    sold = db_session.get(StockItem, first_contract.stock_item_id)
    returned = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{reacquisition_txn.id}").one()
    assert sold.id != returned.id

    # The sold item is history: never reopened, its sale facts untouched.
    assert sold.left_stock_at == dt.datetime(2023, 1, 5, tzinfo=dt.UTC)
    assert sold.effective_price == Decimal("20000.00")
    assert sold.purchase_price is None
    assert sold.pipeline_ref is None
    assert first_contract.gross_price == Decimal("20000.00")

    # The returning car is a new item carrying the reacquisition's own facts.
    assert returned.vehicle_id == mdm.id
    assert returned.lifecycle_status.value == "in_stock"
    assert returned.in_stock_at == dt.datetime(2024, 8, 1, tzinfo=dt.UTC)
    assert returned.left_stock_at is None
    assert returned.supplier_is_vat_registered is True
    assert returned.purchase_price == Decimal("9000.00")
    assert returned.stock_number != sold.stock_number
    assert outcomes_by_txn[reacquisition_txn.id].new_stock_item_id == returned.id
    # KAN-100: the replica records the purchase against the new item, not the sold one.
    replica = db_session.query(SalesStockItemPurchase).one()
    assert replica.stock_item_id == returned.id


def test_sold_traded_back_in_and_sold_again_closes_the_returned_item_not_the_first(db_session):
    """KAN-256: once a VIN has a sold item and a returned one, the second
    sale resolves deterministically to the item still in stock — never to
    the first, already sold one.
    """

    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    first_sale = _sale_transaction(
        db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("20000.00"),
        transaction_date=dt.datetime(2022, 3, 1, tzinfo=dt.UTC),
    )
    trade_in = _trade_in_transaction(
        db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("11000.00"),
        transaction_date=dt.datetime(2023, 5, 1, tzinfo=dt.UTC),
    )
    second_sale = _sale_transaction(
        db_session, customer_id=customer.id, vehicle_id=legacy.id, amount=Decimal("14000.00"),
        transaction_date=dt.datetime(2023, 9, 1, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["migrated", "migrated", "migrated"]
    assert db_session.query(StockItem).filter_by(vin=mdm.vin).count() == 2
    first = db_session.get(
        StockItem, db_session.query(SalesContract).filter_by(legacy_transaction_id=first_sale.id).one().stock_item_id
    )
    second_contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=second_sale.id).one()
    returned = db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{trade_in.id}").one()
    assert second_contract.stock_item_id == returned.id
    assert returned.left_stock_at == dt.datetime(2023, 9, 1, tzinfo=dt.UTC)
    assert returned.effective_price == Decimal("14000.00")
    assert returned.purchase_price == Decimal("11000.00")
    assert first.left_stock_at == dt.datetime(2022, 3, 1, tzinfo=dt.UTC)
    assert first.effective_price == Decimal("20000.00")
    assert second_contract.is_invoiceable is True  # the trade-in booked the returned item's purchase


def _live_stock_item(db_session, mdm, *, stock_number, left_stock_at=None, purchase_price=None) -> StockItem:
    """A stock item live Stock created — not by this import (no
    legacy-transaction pipeline_ref)."""

    item = StockItem(
        tenant_id=TENANT_ID, stock_number=stock_number, vehicle_id=mdm.id, vin=mdm.vin,
        vehicle_label="Alfa Romeo Giulietta (2020)", condition=StockItemCondition.USED,
        lifecycle_status=LifecycleStatus.IN_STOCK, in_stock_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        left_stock_at=left_stock_at, purchase_price=purchase_price,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _snapshot(item: StockItem) -> tuple:
    return (
        item.version, item.left_stock_at, item.in_stock_at, item.purchase_price, item.effective_price,
        item.pipeline_ref, item.supplier_name, item.is_invoiceable,
    )


def test_legacy_rows_never_touch_a_car_live_stock_holds_even_beside_a_sold_row(db_session):
    """KAN-256: historical data is imported before a dealership starts to
    work (Anto, 2026-10-08), so a car live Stock holds can only meet this
    import by mistake. With a VIN carrying both a sold row and a live
    returned row, neither a legacy trade-in nor a legacy sale may pick one
    arbitrarily, reopen the sold row (IntegrityError on the in-stock VIN
    index), close the live car out or overwrite its purchase: each row is
    reported as vehicle_known_to_live_stock and nothing changes.
    """

    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    sold = _live_stock_item(
        db_session, mdm, stock_number="S-OLD", left_stock_at=dt.datetime(2026, 9, 15, tzinfo=dt.UTC),
    )
    returned = _live_stock_item(db_session, mdm, stock_number="S-NEW")
    trade_in = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    sale = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    before = {sold.id: _snapshot(sold), returned.id: _snapshot(returned)}

    report = run_migration(db_session, commit=True)

    assert report.aborted is False
    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[trade_in.id].outcome == "vehicle_known_to_live_stock"
    assert outcomes_by_txn[sale.id].outcome == "vehicle_known_to_live_stock"
    assert str(returned.id) in outcomes_by_txn[trade_in.id].notes
    assert "VEHICLE_KNOWN_TO_LIVE_STOCK" in report.summary()
    db_session.expire_all()
    assert {i.id: _snapshot(i) for i in db_session.query(StockItem).all()} == before
    assert db_session.query(SalesContract).count() == 0
    assert db_session.query(SalesStockItemPurchase).count() == 0


def test_a_legacy_trade_in_never_fills_a_live_item_that_has_no_purchase_yet(db_session):
    """Before KAN-256 a legacy trade-in wrote its years-old purchase facts
    onto any in-stock item without a purchase price — which only live Stock
    can create. Reported instead, never written.
    """

    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    live = _live_stock_item(db_session, mdm, stock_number="S-LIVE")
    trade_in = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    before = _snapshot(live)

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["vehicle_known_to_live_stock"]
    db_session.expire_all()
    assert _snapshot(db_session.get(StockItem, live.id)) == before
    assert db_session.query(StockItem).filter_by(pipeline_ref=f"legacy-transaction:{trade_in.id}").count() == 0


def test_a_legacy_sale_never_closes_out_a_car_live_stock_holds(db_session):
    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    live = _live_stock_item(db_session, mdm, stock_number="S-LIVE", purchase_price=Decimal("10000.00"))
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    before = _snapshot(live)

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["vehicle_known_to_live_stock"]
    db_session.expire_all()
    assert _snapshot(db_session.get(StockItem, live.id)) == before
    assert db_session.query(SalesContract).count() == 0


def test_legacy_rows_never_touch_a_car_live_stock_has_already_sold(db_session):
    """A car live Stock took in and sold, with no item in stock now: a legacy
    trade-in must not create a phantom item in stock (with a years-old
    in_stock_at and a booked purchase) for a car the dealership no longer
    has, and a legacy sale is not mistaken for a second legacy sale. Both
    are reported; nothing is written.
    """

    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    live_sold = _live_stock_item(
        db_session, mdm, stock_number="S-LIVE", purchase_price=Decimal("10000.00"),
        left_stock_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
    )
    trade_in = _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    sale = _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()
    before = _snapshot(live_sold)

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[trade_in.id].outcome == "vehicle_known_to_live_stock"
    assert outcomes_by_txn[sale.id].outcome == "vehicle_known_to_live_stock"
    assert "S-LIVE" in outcomes_by_txn[sale.id].notes
    db_session.expire_all()
    assert db_session.query(StockItem).count() == 1
    assert _snapshot(db_session.get(StockItem, live_sold.id)) == before
    assert db_session.query(SalesContract).count() == 0
    assert db_session.query(SalesStockItemPurchase).count() == 0


def test_dry_run_reports_a_car_live_stock_holds(db_session):
    """The dry run reports a car live Stock holds, as the real run does."""

    _dealership(db_session)
    customer = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    mdm = _migrated_vehicle_mdm(db_session, legacy)
    _live_stock_item(db_session, mdm, stock_number="S-LIVE")
    _trade_in_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    _sale_transaction(db_session, customer_id=customer.id, vehicle_id=legacy.id)
    db_session.commit()

    report = run_migration(db_session, commit=False)

    assert [o.outcome for o in report.outcomes] == ["vehicle_known_to_live_stock", "vehicle_known_to_live_stock"]


def test_two_trade_ins_with_no_intervening_sale_report_the_second_as_a_conflict(db_session):
    """A genuine anomaly: the same vehicle traded in twice with no sale
    in between is not something this migration resolves on your behalf —
    the second row is reported, and the first trade-in's acquisition data
    is left untouched rather than silently overwritten.
    """

    _dealership(db_session)
    first_customer = _customer(db_session)
    second_customer = _customer(db_session, vat_registered=True)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    first_txn = _trade_in_transaction(
        db_session, customer_id=first_customer.id, vehicle_id=legacy.id, amount=Decimal("8000.00"),
        transaction_date=dt.datetime(2024, 1, 10, tzinfo=dt.UTC),
    )
    second_txn = _trade_in_transaction(
        db_session, customer_id=second_customer.id, vehicle_id=legacy.id, amount=Decimal("9500.00"),
        transaction_date=dt.datetime(2024, 2, 20, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[first_txn.id].outcome == "migrated"
    assert outcomes_by_txn[second_txn.id].outcome == "vehicle_already_in_stock"
    assert db_session.query(StockItem).count() == 1
    item = db_session.query(StockItem).one()
    assert item.purchase_price == Decimal("8000.00")  # the first trade-in's data, never overwritten
    assert item.supplier_is_vat_registered is False


def test_two_sales_with_no_intervening_trade_in_report_the_second_as_a_conflict(db_session):
    """The symmetric anomaly on the sale side: the same vehicle sold
    twice with no reacquisition in between. Reported, not overwritten.
    """

    customer_a = _customer(db_session)
    customer_b = _customer(db_session)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
    first_txn = _sale_transaction(
        db_session, customer_id=customer_a.id, vehicle_id=legacy.id, amount=Decimal("18000.00"),
        transaction_date=dt.datetime(2024, 1, 10, tzinfo=dt.UTC),
    )
    second_txn = _sale_transaction(
        db_session, customer_id=customer_b.id, vehicle_id=legacy.id, amount=Decimal("19000.00"),
        transaction_date=dt.datetime(2024, 2, 20, tzinfo=dt.UTC),
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    outcomes_by_txn = {o.transaction_id: o for o in report.outcomes}
    assert outcomes_by_txn[first_txn.id].outcome == "migrated"
    assert outcomes_by_txn[second_txn.id].outcome == "vehicle_already_in_stock"
    assert db_session.query(StockItem).count() == 1
    assert db_session.query(SalesContract).count() == 1
    item = db_session.query(StockItem).one()
    assert item.effective_price == Decimal("18000.00")  # the first sale's price, never overwritten


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
