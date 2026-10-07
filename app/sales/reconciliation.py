"""Sales's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.

Only the replica of Stock's purchase fact (KAN-100) is checked among the
WP-8 tables, both ways round: each replica row names a real stock item, and
each stock item Stock holds as purchased (is_invoiceable) has its replica
row — a purchase Sales never learned of (an event lost, or a purchase
written without one) would otherwise leave the car un-invoiceable in Sales
with no alarm. A confirmed manual configuration still not linked to its
pipeline stock item (KAN-144) is reported too, and (C-F, KAN-10) every configuration an offer or a
contract references must exist. A signed contract whose trade-in valuation is
not stamped «Verwendet» is reported (KAN-115). Otherwise sales_contract's own references
are not checked yet (KAN-145); a replica row whose purchase Stock reversed cannot exist until
Stock emits storno (KAN-146).
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
from app.vehicle.public import Vehicle, VehicleConfiguration

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
    ReferenceCheck(
        label="sales_stock_item_purchase.stock_item_id -> stock_item.id",
        source_model=SalesStockItemPurchase,
        source_row_id_column=SalesStockItemPurchase.id,
        source_fk_column=SalesStockItemPurchase.stock_item_id,
        target_model=StockItem,
        target_id_column=StockItem.id,
    ),
    ReferenceCheck(
        label="stock_item.is_invoiceable -> sales_stock_item_purchase.stock_item_id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.id,
        target_model=SalesStockItemPurchase,
        target_id_column=SalesStockItemPurchase.stock_item_id,
        source_where=lambda: (StockItem.is_invoiceable.is_(True))
        & (StockItem.updated_at < utcnow() - _OUTBOX_LAG_GRACE),
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
    StateCheck(
        # KAN-115 — a confirmation stamps its trade-in valuation before the
        # contract is signed (KAN-101), so a signed contract whose valuation
        # is not stamped was signed before KAN-101, or lost the stamp to
        # another confirmation's compensation in the race ADR-047 leaves to
        # this job. Signed is `signed_at` set: cancelling keeps the stamp
        # (ADR-066). Repaired by hand: «Als verwendet markieren» stamps a
        # valuation a signed contract carries, expired or not. A valuation
        # that does not exist at all is KAN-145's reference check.
        label="signed contract whose trade-in valuation is not used",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        where=lambda: SalesContract.signed_at.is_not(None)
        & select(Valuation.id)
        .where(
            Valuation.tenant_id == SalesContract.tenant_id,
            Valuation.id == SalesContract.trade_in_valuation_id,
            Valuation.used_at.is_(None),
        )
        .exists(),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
