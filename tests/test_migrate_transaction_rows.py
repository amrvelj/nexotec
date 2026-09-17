"""KAN-26 (WP-8 PR-7, ADR-050): migrating the retired `transaction` table's
rows into sales_contract (+ a synthesised offer) for `sale` rows, or a
StockItem carrying the acquisition for `trade_in` rows — see
scripts/migrate_transaction_rows.py's own module docstring for why the
trade-in path is no longer an unconditional rejection, and for the
one-stock-item-per-VIN reuse/reopen behavior between the two row types.
"""

import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal

from app.customer.models.customer import Customer, CustomerType, Language
from app.inventory.models.stock_item import StockItem
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
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
    """The core interaction the module docstring calls out: `stock_item`
    has a real (tenant_id, vin) uniqueness constraint, so a vehicle that
    was both traded in and later resold — two separate completed
    `transaction` rows — cannot get two stock_item rows. Both rows now
    migrate, sharing ONE stock item: the trade-in's acquisition facts and
    the sale's own disposal facts (left_stock_at, resale price) and its
    contract all land on/reference the SAME row, rather than the resale
    being silently walked away from.
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
    assert db_session.query(StockItem).count() == 1  # never two rows for the same VIN

    item = db_session.query(StockItem).one()
    assert item.purchase_price == Decimal("12000.00")  # the trade-in's own acquisition fact, untouched by the sale
    assert item.supplier_is_vat_registered is False
    assert item.left_stock_at is not None  # the resale closed it out
    assert item.effective_price == Decimal("15000.00")  # the resale's own disposal price
    assert item.is_invoiceable is True

    contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=sale_txn.id).one()
    assert contract.stock_item_id == item.id
    assert contract.gross_price == Decimal("15000.00")


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


def test_a_vehicle_sold_then_later_reacquired_as_a_trade_in_reopens_the_same_stock_item(db_session):
    """The reverse, equally ordinary pattern: a vehicle sold once (its
    stock_item closed out), later comes back to the SAME dealer as a
    trade-in from a different customer. Same VIN, so the same stock_item
    row is reopened (not a second, VIN-colliding row) — lifecycle back to
    in_stock, left_stock_at cleared, and the new acquisition's own facts
    recorded, without disturbing the first sale's own, already-confirmed
    contract.
    """

    _dealership(db_session)
    first_owner = _customer(db_session)
    second_owner = _customer(db_session, vat_registered=True)
    legacy = _legacy_vehicle(db_session)
    _migrated_vehicle_mdm(db_session, legacy)
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
    assert db_session.query(StockItem).count() == 1  # reopened, not duplicated

    item = db_session.query(StockItem).one()
    assert item.lifecycle_status.value == "in_stock"  # reopened
    assert item.left_stock_at is None  # no longer known to have left stock
    assert item.supplier_is_vat_registered is True  # the reacquisition's own fact, not the first sale's
    assert item.purchase_price == Decimal("9000.00")
    assert item.pipeline_ref == f"legacy-transaction:{reacquisition_txn.id}"

    # The first sale's own contract is untouched and still points at this item.
    first_contract = db_session.query(SalesContract).filter_by(legacy_transaction_id=first_sale_txn.id).one()
    assert first_contract.stock_item_id == item.id
    assert first_contract.gross_price == Decimal("20000.00")


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
