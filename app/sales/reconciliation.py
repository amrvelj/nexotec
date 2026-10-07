"""Sales's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.

Every cross-context id on sales_offer and sales_contract must resolve
(KAN-145): the dealership, customer, stock item, trade-in vehicle, trade-in
valuation and configurations. A contract's reservation_id names no row of its
own — it is the stock item's active_reservation_id, cleared when the hold is
released — so it is checked only while the contract is confirmed: a signed
contract whose car is no longer held for it could be sold twice.

The replica of Stock's purchase fact (KAN-100) is checked against Stock both
ways round, per dealership (KAN-145): each stock item Stock holds as purchased
(is_invoiceable) has its replica row in its own dealership — a purchase Sales
never learned of (an event lost, or a purchase written without one) would
otherwise leave the car un-invoiceable in Sales with no alarm — and each
replica row's item is purchased in Stock for that same dealership, or Sales
would invoice a car the dealership has not bought. Both match on tenant as
well as item because SalesContract.is_invoiceable does. A confirmed manual
configuration still not linked to its pipeline stock item (KAN-144) is
reported too.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.customer.public import Customer
from app.inventory.public import StockItem
from app.platform.public import Dealership, User
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction
from app.valuation.public import Valuation
from app.vehicle.public import Vehicle, VehicleConfiguration, VehicleMdm

CONTEXT = "sales"

# A fact is published in the same commit that makes it true and reaches Sales
# within outbox lag; only what is still missing after this long alarms.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)

CHECKS: list[ReferenceCheck | StateCheck] = [
    ReferenceCheck(
        label="transaction.tenant_id -> dealership.id",
        source_model=Transaction,
        source_row_id_column=Transaction.id,
        source_fk_column=Transaction.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="transaction.customer_id -> customer.id",
        source_model=Transaction,
        source_row_id_column=Transaction.id,
        source_fk_column=Transaction.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
    ),
    ReferenceCheck(
        label="transaction.vehicle_id -> vehicle.id",
        source_model=Transaction,
        source_row_id_column=Transaction.id,
        source_fk_column=Transaction.vehicle_id,
        target_model=Vehicle,
        target_id_column=Vehicle.id,
    ),
    ReferenceCheck(
        label="transaction.primary_user_id -> user.id",
        source_model=Transaction,
        source_row_id_column=Transaction.id,
        source_fk_column=Transaction.primary_user_id,
        target_model=User,
        target_id_column=User.id,
    ),
    # --- sales_offer (KAN-145) ---
    ReferenceCheck(
        label="sales_offer.tenant_id -> dealership.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="sales_offer.customer_id -> customer.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_offer.stock_item_id -> stock_item.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.stock_item_id,
        target_model=StockItem,
        target_id_column=StockItem.id,
        nullable=True,
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="sales_offer.configuration_id -> vehicle_configuration.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_offer.trade_in_vehicle_id -> vehicle_mdm.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.trade_in_vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=True,
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="sales_offer.trade_in_configuration_id -> vehicle_configuration.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.trade_in_configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_offer.trade_in_valuation_id -> valuation.id",
        source_model=SalesOffer,
        source_row_id_column=SalesOffer.id,
        source_fk_column=SalesOffer.trade_in_valuation_id,
        target_model=Valuation,
        target_id_column=Valuation.id,
        nullable=True,
    ),
    # --- sales_contract (KAN-145) ---
    ReferenceCheck(
        label="sales_contract.tenant_id -> dealership.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="sales_contract.customer_id -> customer.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_contract.stock_item_id -> stock_item.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.stock_item_id,
        target_model=StockItem,
        target_id_column=StockItem.id,
        nullable=True,
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="sales_contract.configuration_id -> vehicle_configuration.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_contract.trade_in_vehicle_id -> vehicle_mdm.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.trade_in_vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=True,
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="sales_contract.trade_in_configuration_id -> vehicle_configuration.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.trade_in_configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_contract.trade_in_valuation_id -> valuation.id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.trade_in_valuation_id,
        target_model=Valuation,
        target_id_column=Valuation.id,
        nullable=True,
    ),
    ReferenceCheck(
        # Released holds are cleared on the stock item, so only a confirmed
        # contract's hold must still be there (a cancelled contract keeps
        # the id of the hold it released). Null on a manual configuration,
        # whose pipeline item Stock reserves itself (KAN-158).
        label="sales_contract.reservation_id -> stock_item.active_reservation_id",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        source_fk_column=SalesContract.reservation_id,
        target_model=StockItem,
        target_id_column=StockItem.active_reservation_id,
        nullable=True,
        source_where=lambda: SalesContract.status == ContractStatus.CONFIRMED,
    ),
    StateCheck(
        # KAN-144 — Stock names the contract on inventory.stock_item.added
        # within outbox lag of the confirmation. A confirmed manual
        # configuration still unlinked after that can never be invoiced (a
        # lost event, or a contract confirmed before KAN-144: KAN-159).
        label="confirmed manual configuration with no pipeline stock item",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        where=lambda: (SalesContract.vehicle_source == "manual")
        & (SalesContract.status == ContractStatus.CONFIRMED)
        & SalesContract.stock_item_id.is_(None)
        & (SalesContract.signed_at < utcnow() - _OUTBOX_LAG_GRACE),
    ),
    # --- the purchase replica against Stock's fact (KAN-100, KAN-145) ---
    ReferenceCheck(
        label="sales_stock_item_purchase.stock_item_id -> stock_item.id",
        source_model=SalesStockItemPurchase,
        source_row_id_column=SalesStockItemPurchase.id,
        source_fk_column=SalesStockItemPurchase.stock_item_id,
        target_model=StockItem,
        target_id_column=StockItem.id,
    ),
    StateCheck(
        label="stock item purchased in Stock with no purchase replica in its dealership",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: StockItem.is_invoiceable.is_(True)
        & (StockItem.updated_at < utcnow() - _OUTBOX_LAG_GRACE)
        & ~select(SalesStockItemPurchase.id)
        .where(
            SalesStockItemPurchase.stock_item_id == StockItem.id,
            SalesStockItemPurchase.tenant_id == StockItem.tenant_id,
        )
        .correlate(StockItem)
        .exists(),
    ),
    StateCheck(
        # The item exists (a missing one is the reference check above) but is
        # not purchased in Stock for the replica's dealership. No grace: the
        # replica only follows Stock's fact, and Stock never sets
        # is_invoiceable back until it emits storno (KAN-146), whose lag this
        # check must then allow for.
        label="purchase replica whose stock item is not purchased in Stock for its dealership",
        source_model=SalesStockItemPurchase,
        source_row_id_column=SalesStockItemPurchase.id,
        where=lambda: select(StockItem.id)
        .where(StockItem.id == SalesStockItemPurchase.stock_item_id)
        .correlate(SalesStockItemPurchase)
        .exists()
        & ~select(StockItem.id)
        .where(
            StockItem.id == SalesStockItemPurchase.stock_item_id,
            StockItem.tenant_id == SalesStockItemPurchase.tenant_id,
            StockItem.is_invoiceable.is_(True),
        )
        .correlate(SalesStockItemPurchase)
        .exists(),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
