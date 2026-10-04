"""Sales's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.

Only the replica of Stock's purchase fact (KAN-100) is checked among the
WP-8 tables, both ways round: each replica row names a real stock item, and
each stock item Stock holds as purchased (is_invoiceable) has its replica
row — a purchase Sales never learned of (an event lost, or a purchase
written without one) would otherwise leave the car un-invoiceable in Sales
with no alarm. sales_contract's own references are not checked yet
(KAN-145); a replica row whose purchase Stock reversed cannot exist until
Stock emits storno (KAN-146).
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.public import Customer
from app.inventory.public import StockItem
from app.platform.public import Dealership, User
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction
from app.vehicle.public import Vehicle

CONTEXT = "sales"

# A purchase is published in the same commit that marks the item and reaches
# Sales within outbox lag; only items left unmirrored longer than this alarm.
_PURCHASE_REPLICATION_GRACE = dt.timedelta(hours=1)

CHECKS = [
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
        & (StockItem.updated_at < utcnow() - _PURCHASE_REPLICATION_GRACE),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
