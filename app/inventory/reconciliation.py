"""Inventory's outbound cross-context references (WP-7 PR-5). Everything
here is read-only — see app.core.reconciliation for the mechanism.

reserved_by_contract_id has no check yet — there is no Contract model
anywhere to check against (app.sales has no "contract" concept today; see
app.inventory.services.pipeline's own docstring). Add one once WP-8 ships
a real sales_contract table, per ADR-050.

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
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
