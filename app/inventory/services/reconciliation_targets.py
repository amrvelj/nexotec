"""What another context's nightly reconciliation checks against Stock's
`stock_item` table (rule 10; ADR-107), built here so the mapped `StockItem`
class never leaves the inventory context (KAN-123 step 3, ADR-162).

Sales, Customers and Vehicle hold references to a stock item (a deal's
`stock_item_id`, a contract's `reservation_id`, Sales' purchase replica,
Customers' bookings, Vehicle's stock-holding projection). Their
`reconciliation.py` asks these questions of Stock's rows; each is a builder
here that reads nothing itself — the reconciliation run executes it:

- `stock_item_reference_check` — the id names a stock item (an orphan
  otherwise); `by_reservation=True` checks a hold id against the item's
  `active_reservation_id` instead.
- `stock_item_exists` — an EXISTS for the id, optionally within one Betrieb,
  optionally only while Stock holds the car as purchased (`is_invoiceable`).
- `stock_item_reservation_exists` — the same for a hold id.
- `stock_item_prices_moved` — the car changed after a deal froze it, and its
  effective or list price differs from the frozen (and the accepted) prices.
- `stock_item_differs_from_holding` — Vehicle's projection row disagrees with
  the stock item it mirrors.
- `purchased_stock_item_check` / `held_stock_item_check` — StateChecks whose
  rows are stock items: purchased with no replica, holding a record with no
  projection row. The caller supplies its own side of the question as a
  `Select` over its own table; the builder correlates it to the stock item.

A stock item is Betrieb-owned (readable group-wide only through Stock's group
listing, ADR-014), and an EXISTS without a Betrieb embedded in a live query
would answer whether another Betrieb holds a car, out of
`test_no_cross_context_orm_query.py`'s sight. So these builders are confined
to the reconciliation modules, which only report
(`tests/architecture/test_inventory_reconciliation_targets_only_in_reconciliation.py`).
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, Numeric, Select, cast, or_, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.base import utcnow
from app.core.reconciliation import ReferenceCheck, StateCheck
from app.inventory.models.stock_item import StockItem

_Column = ColumnElement[Any] | InstrumentedAttribute[Any]


def stock_item_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
    by_reservation: bool = False,
) -> ReferenceCheck:
    """The reference `source_fk_column` holds names a stock item — by its id, or
    by its current hold (`active_reservation_id`) when `by_reservation`."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=StockItem,
        target_id_column=StockItem.active_reservation_id if by_reservation else StockItem.id,
        nullable=nullable,
        source_where=source_where,
    )


def stock_item_exists(stock_item_id: _Column, *, tenant_id: _Column | None = None, purchased: bool = False) -> Exists:
    """A stock item with this id exists — in `tenant_id`'s Betrieb when one is
    given, and held as purchased there (`is_invoiceable`) when `purchased`."""

    query = select(StockItem.id).where(StockItem.id == stock_item_id)
    if tenant_id is not None:
        query = query.where(StockItem.tenant_id == tenant_id)
    if purchased:
        query = query.where(StockItem.is_invoiceable.is_(True))
    return query.exists()


def stock_item_reservation_exists(reservation_id: _Column, *, tenant_id: _Column | None = None) -> Exists:
    """A stock item currently holds this reservation — in `tenant_id`'s Betrieb when one is given."""

    query = select(StockItem.id).where(StockItem.active_reservation_id == reservation_id)
    if tenant_id is not None:
        query = query.where(StockItem.tenant_id == tenant_id)
    return query.exists()


def _prices_differ(prices: Any) -> ColumnElement[bool]:
    """The stock item's prices differ from a frozen `{effectivePrice, listPrice}` JSON block."""

    return or_(
        cast(prices["effectivePrice"].as_string(), Numeric(12, 2)).is_distinct_from(StockItem.effective_price),
        cast(prices["listPrice"].as_string(), Numeric(12, 2)).is_distinct_from(StockItem.list_price),
    )


def stock_item_prices_moved(
    stock_item_id: _Column,
    *,
    tenant_id: _Column,
    changed_after: _Column,
    changed_before: dt.datetime,
    frozen_prices: Any,
    accepted_at: _Column,
    accepted_prices: Any,
) -> Exists:
    """The Betrieb's stock item changed after `changed_after` and before
    `changed_before`, and its effective or list price differs from
    `frozen_prices` — and, once `accepted_at` is set, from `accepted_prices`
    too (KAN-553: what the seller accepted with «Ignorieren»)."""

    return (
        select(StockItem.id)
        .where(
            StockItem.id == stock_item_id,
            StockItem.tenant_id == tenant_id,
            StockItem.updated_at > changed_after,
            StockItem.updated_at < changed_before,
            _prices_differ(frozen_prices),
            or_(accepted_at.is_(None), _prices_differ(accepted_prices)),
        )
        .exists()
    )


def stock_item_differs_from_holding(
    stock_item_id: _Column,
    *,
    tenant_id: _Column,
    left_stock_at: _Column,
    vehicle_id: _Column,
    changed_before: dt.datetime,
) -> Exists:
    """The stock item, unchanged since `changed_before`, is of another Betrieb
    than `tenant_id`, has left stock where `left_stock_at` says not (or the
    reverse), or — still in stock — names another record than `vehicle_id`
    (KAN-667: Vehicle's projection of the records a Betrieb holds)."""

    return (
        select(StockItem.id)
        .where(
            StockItem.id == stock_item_id,
            StockItem.updated_at < changed_before,
            (StockItem.tenant_id != tenant_id)
            | (StockItem.left_stock_at.is_(None) != left_stock_at.is_(None))
            | (StockItem.left_stock_at.is_(None) & StockItem.vehicle_id.is_distinct_from(vehicle_id)),
        )
        .exists()
    )


_Counterpart = Callable[[InstrumentedAttribute[Any], InstrumentedAttribute[Any]], Select[Any]]


def purchased_stock_item_check(*, label: str, grace: dt.timedelta, replica: _Counterpart) -> StateCheck:
    """A stock item Stock holds as purchased (`is_invoiceable`), last changed
    more than `grace` ago, for which `replica(stock_item_id, tenant_id)` —
    the caller's select over its own table — finds no row. The finding names
    the stock item."""

    return StateCheck(
        label=label,
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: StockItem.is_invoiceable.is_(True)
        & (StockItem.updated_at < utcnow() - grace)
        & ~replica(StockItem.id, StockItem.tenant_id).correlate(StockItem).exists(),
    )


def held_stock_item_check(*, label: str, grace: dt.timedelta, holding: _Counterpart) -> StateCheck:
    """A stock item that names a vehicle-mdm record and has not left stock,
    last changed more than `grace` ago, for which
    `holding(stock_item_id, tenant_id)` — the caller's select
    over its own table — finds no row. The finding names the stock item."""

    return StateCheck(
        label=label,
        source_model=StockItem,
        source_row_id_column=StockItem.id,
        where=lambda: StockItem.vehicle_id.is_not(None)
        & StockItem.left_stock_at.is_(None)
        & (StockItem.updated_at < utcnow() - grace)
        & ~holding(StockItem.id, StockItem.tenant_id).correlate(StockItem).exists(),
    )
