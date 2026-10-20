"""Sales's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.

Every cross-context id on sales_offer and sales_contract must resolve
(KAN-145): the dealership, the customer, stock item, trade-in vehicle,
trade-in valuation and configurations. A contract's reservation_id names no
row of its own — it is the stock item's active_reservation_id, cleared when
the hold is released — so it is checked only while the contract is
confirmed: a signed contract whose car is no longer held for it could be
sold twice. So must every one on Sales' other tables (KAN-243): each row's
dealership, the deal projection's customer and the user who rendered a
document. Offer, contract and owner ids inside Sales are Sales' own.

Who wrote each row — created_by / updated_by on every table that has them —
must name a platform user, or be null for a system or legacy write
(KAN-243; Anto, 2026-10-12: everywhere). Those checks come from
app.platform.public.actor_reference_checks, one per column, for every
table in ACTOR_TABLES; tests/architecture/test_actor_columns_reconciled.py
fails when a table with those columns is missing from it.

Resolving on id is not enough where the target belongs to one dealership
(KAN-259): Sales reads a stock item, a configuration and a valuation only in
the deal's own dealership (is_invoiceable, reserve_for_contract,
get_configuration_for_host, the valuation stamp — valuations are
tenant-private, ADR-029), and a customer only in the dealership's group
(customers are group-scoped, ADR-014). Each such reference is checked twice,
for two different defects: the ReferenceCheck reports an id that resolves to
nothing at all (orphan), the StateCheck one that resolves only outside the
deal's dealership or group (mismatch). The StateCheck asks for the target to
exist first, so a row is never reported by both.

The replica of Stock's purchase fact (KAN-100) is checked against Stock both
ways round, per dealership (KAN-145): each stock item Stock holds as purchased
(is_invoiceable) has its replica row in its own dealership — a purchase Sales
never learned of (an event lost, or a purchase written without one) would
otherwise leave the car un-invoiceable in Sales with no alarm — and each
replica row's item is purchased in Stock for that same dealership, or Sales
would invoice a car the dealership has not bought. Both match on tenant as
well as item because SalesContract.is_invoiceable does. A confirmed manual
configuration still not linked to its pipeline stock item (KAN-144) is
reported too, and so is a signed contract whose trade-in valuation is not
stamped «Verwendet» (KAN-115).
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.customer.public import Customer, customer_in_group
from app.inventory.public import StockItem
from app.platform.public import Dealership, User, actor_reference_checks
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal, SalesNumberSequence
from app.sales.models.document import SalesDocument
from app.sales.models.line_item import SalesLineItem
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.models.stock_item_purchase import SalesStockItemPurchase
from app.sales.models.transaction import Transaction
from app.valuation.public import Valuation
from app.vehicle.public import Vehicle, VehicleConfiguration, VehicleMdm

CONTEXT = "sales"

# A fact is published in the same commit that makes it true and reaches Sales
# within outbox lag; only what is still missing after this long alarms.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)

# Every Sales table with created_by / updated_by (KAN-243).
ACTOR_TABLES = (Transaction, SalesOffer, SalesContract, SalesDeal, SalesLineItem, SalesDocument)

_Deal = type[SalesOffer] | type[SalesContract]


def _in_another_dealership(
    label: str,
    deal: _Deal,
    column: InstrumentedAttribute[Any],
    target_id: InstrumentedAttribute[Any],
    target_tenant_id: InstrumentedAttribute[Any],
    *,
    only_where: Callable[[], ColumnElement[bool]] | None = None,
) -> StateCheck:
    """KAN-259 — the deal's `column` names a target that exists, but none in
    the deal's own dealership. A target that does not exist at all is the
    id-only ReferenceCheck's finding, not this one's."""

    def where() -> ColumnElement[bool]:
        exists_anywhere = select(target_id).where(target_id == column).correlate(deal).exists()
        exists_in_own_dealership = (
            select(target_id)
            .where(target_id == column, target_tenant_id == deal.tenant_id)
            .correlate(deal)
            .exists()
        )
        condition = exists_anywhere & ~exists_in_own_dealership
        return condition if only_where is None else only_where() & condition

    return StateCheck(label=label, source_model=deal, source_row_id_column=deal.id, where=where)


def _customer_outside_the_group(label: str, deal: _Deal) -> StateCheck:
    """KAN-259 — customers are group-scoped (ADR-014): the deal's customer
    exists, but not in the group of the deal's dealership. A deal whose
    dealership is itself missing is the tenant_id ReferenceCheck's finding."""

    def where() -> ColumnElement[bool]:
        customer_exists = select(Customer.id).where(Customer.id == deal.customer_id).correlate(deal).exists()
        dealership_exists = select(Dealership.id).where(Dealership.id == deal.tenant_id).correlate(deal).exists()
        dealerships_group = (
            select(Dealership.dealer_group_id).where(Dealership.id == deal.tenant_id).correlate(deal).scalar_subquery()
        )
        in_group = customer_in_group(deal.customer_id, dealerships_group).correlate(deal)
        return customer_exists & dealership_exists & ~in_group

    return StateCheck(label=label, source_model=deal, source_row_id_column=deal.id, where=where)


def _owned_by_a_deactivated_user(deal: _Deal, open_statuses: tuple[Any, ...]) -> StateCheck:
    """KAN-200 (FR-A-37, rule 10): an open deal whose responsible seller is
    deactivated — a hand-over that failed after the status change (the
    expiry job's retry window may have passed), or an owner set after they
    left. The deactivation step moves every open record; this says when it
    did not. A seller who no longer exists is the owner ReferenceCheck's."""

    table = deal.__tablename__

    def where() -> ColumnElement[bool]:
        deactivated = (
            select(User.id).where(User.id == deal.owner_user_id, User.status == "deactivated").correlate(deal).exists()
        )
        return deal.status.in_(open_statuses) & deactivated

    return StateCheck(
        label=f"{table}.owner_user_id names a deactivated user while the {table.removeprefix('sales_')} is open",
        source_model=deal,
        source_row_id_column=deal.id,
        where=where,
    )


def _same_dealership_checks(deal: _Deal) -> list[StateCheck]:
    table = deal.__tablename__
    return [
        _customer_outside_the_group(f"{table}.customer_id names a customer outside its dealership's group", deal),
        _in_another_dealership(
            f"{table}.stock_item_id names another dealership's stock item",
            deal, deal.stock_item_id, StockItem.id, StockItem.tenant_id,
        ),
        _in_another_dealership(
            f"{table}.configuration_id names another dealership's configuration",
            deal, deal.configuration_id, VehicleConfiguration.id, VehicleConfiguration.tenant_id,
        ),
        _in_another_dealership(
            f"{table}.trade_in_configuration_id names another dealership's configuration",
            deal, deal.trade_in_configuration_id, VehicleConfiguration.id, VehicleConfiguration.tenant_id,
        ),
        _in_another_dealership(
            f"{table}.trade_in_valuation_id names another dealership's valuation",
            deal, deal.trade_in_valuation_id, Valuation.id, Valuation.tenant_id,
        ),
    ]


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
    # KAN-259 — reserve_for_contract holds a car of the contract's own
    # dealership; a hold found only on another dealership's item is not
    # this contract's.
    _in_another_dealership(
        "sales_contract.reservation_id names a hold on another dealership's stock item",
        SalesContract, SalesContract.reservation_id, StockItem.active_reservation_id, StockItem.tenant_id,
        only_where=lambda: SalesContract.status == ContractStatus.CONFIRMED,
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
    # --- Sales' other tables (KAN-243) ---
    ReferenceCheck(
        label="sales_deal.tenant_id -> dealership.id",
        source_model=SalesDeal,
        source_row_id_column=SalesDeal.id,
        source_fk_column=SalesDeal.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="sales_deal.customer_id -> customer.id",
        source_model=SalesDeal,
        source_row_id_column=SalesDeal.id,
        source_fk_column=SalesDeal.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_line_item.tenant_id -> dealership.id",
        source_model=SalesLineItem,
        source_row_id_column=SalesLineItem.id,
        source_fk_column=SalesLineItem.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="sales_document.tenant_id -> dealership.id",
        source_model=SalesDocument,
        source_row_id_column=SalesDocument.id,
        source_fk_column=SalesDocument.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        # The signed-in user who generated it; null for a system render.
        label="sales_document.rendered_by -> user.id",
        source_model=SalesDocument,
        source_row_id_column=SalesDocument.id,
        source_fk_column=SalesDocument.rendered_by,
        target_model=User,
        target_id_column=User.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="sales_stock_item_purchase.tenant_id -> dealership.id",
        source_model=SalesStockItemPurchase,
        source_row_id_column=SalesStockItemPurchase.id,
        source_fk_column=SalesStockItemPurchase.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        # Keyed by (tenant_id, series): the finding names the row by its
        # tenant_id, the only id it has.
        label="sales_number_sequence.tenant_id -> dealership.id",
        source_model=SalesNumberSequence,
        source_row_id_column=SalesNumberSequence.tenant_id,
        source_fk_column=SalesNumberSequence.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
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
    StateCheck(
        # KAN-115 — a confirmation stamps its trade-in valuation before the
        # contract is signed (KAN-101), so a signed contract whose valuation
        # is not stamped was signed before KAN-101, or lost the stamp to
        # another confirmation's compensation in the race ADR-047 leaves to
        # this job. Signed is `signed_at` set: cancelling keeps the stamp
        # (ADR-066). Repaired by hand: «Als verwendet markieren» stamps a
        # valuation a signed contract carries, expired or not. Only a
        # valuation of the contract's own dealership counts (tenant-private,
        # ADR-029; the hand stamp looks for contracts there too): one that
        # does not exist at all is KAN-145's reference check; one of another
        # dealership is KAN-259's tenant check below.
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
    # --- each reference in the deal's own dealership or group (KAN-259) ---
    *_same_dealership_checks(SalesOffer),
    *_same_dealership_checks(SalesContract),
    # --- who wrote each row (KAN-243) ---
    *actor_reference_checks(*ACTOR_TABLES),
    # --- the responsible seller (KAN-200): a platform User id on three tables ---
    *(
        ReferenceCheck(
            label=f"{model.__tablename__}.owner_user_id -> user.id",
            source_model=model,
            source_row_id_column=model.id,
            source_fk_column=model.owner_user_id,
            target_model=User,
            target_id_column=User.id,
            nullable=True,  # unassigned is a state, not a dangling reference
        )
        for model in (SalesOffer, SalesContract, SalesDeal)
    ),
    _owned_by_a_deactivated_user(SalesOffer, (OfferStatus.DRAFT, OfferStatus.OPEN)),
    _owned_by_a_deactivated_user(SalesContract, (ContractStatus.PENDING,)),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
