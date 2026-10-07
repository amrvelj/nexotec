"""Inventory's outbound cross-context references (WP-7 PR-5; the
reserved_by_contract_id check added WP-8 PR-6; the cancelled-contract
record's two checks added KAN-158; the valuation pointer's two KAN-115).
Everything here is read-only — see app.core.reconciliation for the mechanism.

The invoicing-gate invariant (is_invoiceable vs. a real
finance.invoice.issued fact) is NOT a ReferenceCheck — it isn't a dangling
foreign key, it's a state-consistency check against an event, handled
per-event by app.inventory.services.invoicing_gate.
apply_finance_invoice_issued instead.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.stock_item import StockItem
from app.platform.public import Dealership, Location
from app.sales.public import SalesContract
from app.valuation.public import Valuation
from app.vehicle.public import VehicleConfiguration, VehicleMdm

CONTEXT = "inventory"

CHECKS: list[ReferenceCheck | StateCheck] = [
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
    ReferenceCheck(
        label="inventory_cancelled_contract.tenant_id -> dealership.id",
        source_model=InventoryCancelledContract,
        source_row_id_column=InventoryCancelledContract.id,
        source_fk_column=InventoryCancelledContract.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="inventory_cancelled_contract.contract_id -> sales_contract.id",
        source_model=InventoryCancelledContract,
        source_row_id_column=InventoryCancelledContract.id,
        source_fk_column=InventoryCancelledContract.contract_id,
        target_model=SalesContract,
        target_id_column=SalesContract.id,
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="stock_item.configuration_id -> vehicle_configuration.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    # KAN-115 — the pointer a trade-in's pipeline item copies from its
    # valuation when Stock creates it (KAN-101, services/pipeline.py).
    ReferenceCheck(
        label="stock_item.valuation_ref_id -> valuation.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.valuation_ref_id,
        target_model=Valuation,
        target_id_column=Valuation.id,
        nullable=True,  # set only on a trade-in item whose contract carried a valuation
    ),
    StateCheck(
        # A valuation is never edited after creation (ADR-066), so a copied
        # amount that differs from it — or is missing — is a copy gone wrong.
        label="stock_item.valuation_ref_amount differs from valuation.final_offer",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: select(Valuation.id)
        .where(
            Valuation.id == StockItem.valuation_ref_id,
            Valuation.final_offer.is_distinct_from(StockItem.valuation_ref_amount),
        )
        .exists(),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
