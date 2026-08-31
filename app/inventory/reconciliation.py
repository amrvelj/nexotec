"""Inventory's outbound cross-context references (WP-7 PR-5; the
reserved_by_contract_id check added WP-8 PR-6). Everything here is
read-only — see app.core.reconciliation for the mechanism.

The invoicing-gate invariant (is_invoiceable vs. a real
finance.invoice.issued fact) is NOT a ReferenceCheck — it isn't a dangling
foreign key, it's a state-consistency check against an event, handled
per-event by app.inventory.services.invoicing_gate.
apply_finance_invoice_issued instead.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.inventory.models.stock_item import StockItem
from app.platform.public import Dealership, Location
from app.sales.public import SalesContract
from app.vehicle.public import VehicleMdm

CONTEXT = "inventory"

CHECKS = [
    ReferenceCheck(
        label="stock_item.tenant_id -> dealership.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="stock_item.vehicle_id -> vehicle_mdm.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=True,  # null while lifecycle_status='pipeline' (ADR-045)
    ),
    ReferenceCheck(
        label="stock_item.location_id -> location.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.location_id,
        target_model=Location,
        target_id_column=Location.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="stock_item.reserved_by_contract_id -> sales_contract.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.reserved_by_contract_id,
        target_model=SalesContract,
        target_id_column=SalesContract.id,
        nullable=True,  # only set while reservation_state='reserved'
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
