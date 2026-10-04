"""Sales's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.

Only the replica of Stock's purchase fact (KAN-100) is checked among the
WP-8 tables; sales_contract's own references are not yet (a Kanban ticket
tracks it, together with comparing the replica against Stock's fact).
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.public import Customer
from app.inventory.public import StockItem
from app.platform.public import Dealership, User
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction
from app.vehicle.public import Vehicle

CONTEXT = "sales"

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
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
