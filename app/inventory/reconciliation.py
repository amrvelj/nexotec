"""Inventory's outbound cross-context references (WP-7 PR-5; the
reserved_by_contract_id check added WP-8 PR-6; the cancelled-contract
record's two checks added KAN-158; the valuation pointer's two KAN-115; the
contract projection's four KAN-630).
Everything here is read-only — see app.core.reconciliation for the mechanism.

The invoicing-gate invariant (is_invoiceable vs. a real
finance.invoice.issued fact) is NOT a ReferenceCheck — it isn't a dangling
foreign key, it's a state-consistency check against an event, handled
per-event by app.inventory.services.invoicing_gate.
apply_finance_invoice_issued instead.
"""

import datetime as dt

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.customer.public import Customer
from app.inventory.models.cancelled_contract import InventoryCancelledContract
from app.inventory.models.sales_contract import InventorySalesContract
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_ledger import StockItemLedger
from app.inventory.models.stock_item_publishing import StockItemPublishing
from app.platform.public import Dealership, Location, actor_reference_checks
from app.sales.public import SalesContract
from app.valuation.public import Valuation
from app.vehicle.public import VehicleConfiguration, VehicleMdm

CONTEXT = "inventory"

# Every Inventory table with created_by / updated_by: each must name a
# platform user, or be null (KAN-243; see
# app.platform.public.actor_reference_checks). The ledger has created_by only.
ACTOR_TABLES = (StockItem, StockItemLedger, StockItemPublishing)
# InventorySalesContract (KAN-630) carries no actor column: a consumer and a
# backfill write it, never a user.

# KAN-633: how long a host's `configuration_attached` fact may be in flight.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)


def _names_an_untaken_configuration() -> StateCheck:
    """KAN-633 (ADR-131, rule 10): the row names a configuration vehicle
    still counts as never taken (`first_taken_at` null) an hour after the
    row last changed — the host's `configuration_attached` fact was not
    consumed (dead-lettered, or the consumer is stuck), so vehicle's purge
    could delete it the day its safety net misses it. The hour is the
    outbox-lag grace: a fact in flight is not a finding. Vehicle names no
    host; each host reports its own rows. A configuration that no longer
    exists is the reference check's finding, not this one's."""

    return StateCheck(
        label="stock_item.configuration_id names a configuration vehicle has not stamped as taken",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: select(VehicleConfiguration.id)
        .where(VehicleConfiguration.id == StockItem.configuration_id, VehicleConfiguration.first_taken_at.is_(None))
        .correlate(StockItem)
        .exists()
        & (StockItem.updated_at < utcnow() - _OUTBOX_LAG_GRACE),
    )


def _vin_differs_from_its_vehicle() -> StateCheck:
    """KAN-557 (ADR-116 (3), rule 10): a car still in stock whose VIN is not
    its vehicle-mdm record's — three records describing two cars. «VIN
    korrigieren» rewrites both in two transactions; a failed compensation,
    an identity PATCH on the Vehicle 360, or a correction from before the
    action leaves this behind. A car that left stock keeps the VIN it was
    sold under (dated evidence) and is not reported."""

    return StateCheck(
        label="stock_item.vin differs from its vehicle_mdm.vin",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: select(VehicleMdm.id)
        .where(VehicleMdm.id == StockItem.vehicle_id, VehicleMdm.vin.is_distinct_from(StockItem.vin))
        .correlate(StockItem)
        .exists()
        & StockItem.left_stock_at.is_(None),
    )


def _vin_differs_from_its_configuration() -> StateCheck:
    """KAN-557 (KAN-544): a bought car still in stock whose configuration
    names another VIN — the configurator edited it away from the car's
    (before KAN-557 nothing refused that) or a correction's configuration leg
    failed. A configuration without a VIN (a factory order) names no car;
    a pipeline car never bought has no record yet and its configuration's VIN
    wins at booking (KAN-575), so neither is reported."""

    return StateCheck(
        label="stock_item.vin differs from its configuration's vin",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: select(VehicleConfiguration.id)
        .where(
            VehicleConfiguration.id == StockItem.configuration_id,
            VehicleConfiguration.vin.is_not(None),
            VehicleConfiguration.vin.is_distinct_from(StockItem.vin),
        )
        .correlate(StockItem)
        .exists()
        & StockItem.vehicle_id.is_not(None)
        & StockItem.left_stock_at.is_(None),
    )


def _projection_differs_from_its_contract() -> StateCheck:
    """KAN-630 (rule 10): Stock's projection row says something else than the
    contract it mirrors — status, Betrieb, whether the invoice was requested,
    or the car it was requested for — although the contract last changed
    over an hour ago: a contract fact was missed (dead-lettered, or the
    consumer is stuck). The sweep and «Einkauf stornieren» would read the
    stale answer. A contract changed within the hour may still have its fact
    in flight, so it is not a finding yet."""

    return StateCheck(
        label="inventory_sales_contract differs from its sales_contract",
        source_model=InventorySalesContract,
        source_row_id_column=InventorySalesContract.id,
        where=lambda: select(SalesContract.id)
        .where(
            SalesContract.id == InventorySalesContract.contract_id,
            SalesContract.updated_at < utcnow() - _OUTBOX_LAG_GRACE,
            (SalesContract.tenant_id != InventorySalesContract.tenant_id)
            | (cast(SalesContract.status, String) != cast(InventorySalesContract.status, String))
            | (SalesContract.invoice_requested_at.is_(None) != InventorySalesContract.invoice_requested_at.is_(None))
            | (
                SalesContract.invoice_requested_at.is_not(None)
                & SalesContract.stock_item_id.is_distinct_from(InventorySalesContract.stock_item_id)
            ),
        )
        .correlate(InventorySalesContract)
        .exists(),
    )


def _contract_missing_from_the_projection() -> StateCheck:
    """KAN-630 (rule 10): a contract created over an hour ago that Stock's
    projection does not hold — its facts were all missed, or the projection
    backfill has not run since the deploy. The finding names the contract."""

    return StateCheck(
        label="sales_contract missing from inventory_sales_contract",
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        where=lambda: (SalesContract.created_at < utcnow() - _OUTBOX_LAG_GRACE)
        & ~select(InventorySalesContract.id)
        .where(
            InventorySalesContract.contract_id == SalesContract.id,
            InventorySalesContract.tenant_id == SalesContract.tenant_id,
        )
        .correlate(SalesContract)
        .exists(),
    )


CHECKS: list[ReferenceCheck | StateCheck] = [
    _names_an_untaken_configuration(),
    _vin_differs_from_its_vehicle(),
    _vin_differs_from_its_configuration(),
    _projection_differs_from_its_contract(),
    _contract_missing_from_the_projection(),
    ReferenceCheck(
        label="inventory_sales_contract.contract_id -> sales_contract.id",
        source_model=InventorySalesContract,
        source_row_id_column=InventorySalesContract.id,
        source_fk_column=InventorySalesContract.contract_id,
        target_model=SalesContract,
        target_id_column=SalesContract.id,
    ),
    ReferenceCheck(
        label="inventory_sales_contract.tenant_id -> dealership.id",
        source_model=InventorySalesContract,
        source_row_id_column=InventorySalesContract.id,
        source_fk_column=InventorySalesContract.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
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
    # KAN-632 — who traded the car in, kept from Sales' confirmation for the
    # item's purchase and storno events.
    ReferenceCheck(
        label="stock_item.trade_in_customer_id -> customer.id",
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        source_fk_column=StockItem.trade_in_customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,  # set only on a trade-in's pipeline item created since KAN-632
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
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
