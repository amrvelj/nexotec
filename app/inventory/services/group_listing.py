"""ADR-055 group-readable stock listing (WP-7 PR-7).

Stock's tenant scope is the DEALERSHIP, not the group (unlike Customer,
which is natively group-scoped per ADR-014) — so this is a genuine
cross-tenant read, gated the same way app.core.tenancy.
get_group_read_or_404 gates a single-row one: caller's own group only,
behind DealerGroup.group_read_enabled, 404 (never 403) on either check
failing. There is no single "row" to fetch here (this is a roster, not
one entity), so this hand-rolls the same two checks rather than calling
that function directly — see tests/architecture/test_no_ambient_group_read.py,
which polices a literal filter-predicate pattern specifically scoped to
Customer's own native group field; this module's own filter is on
Dealership.dealer_group_id, a structurally different column for a
context that was never group-scoped to begin with.
"""

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.inventory.models.stock_item import StockItem
from app.platform.public import DealerGroup, Dealership


def list_group_stock_items(
    db: Session, *, principal_group_id: uuid.UUID, requested_group_id: uuid.UUID, is_authorized: Callable[[], bool]
) -> list[tuple[StockItem, Dealership]]:
    """Returns (item, dealership) pairs so the caller can build
    dealershipLabel without a second round trip. 404s — never 403s — on:
    a group_id the caller doesn't belong to, a group with group_read_enabled
    still off, or a caller whose role doesn't grant this read at all
    (is_authorized, supplied by the API layer, same shape as
    get_group_read_or_404's own parameter).
    """

    if not is_authorized() or principal_group_id != requested_group_id:
        raise NotFoundError(f"Dealer group {requested_group_id} was not found.")

    group = db.get(DealerGroup, requested_group_id)
    if group is None or not group.group_read_enabled:
        raise NotFoundError(f"Dealer group {requested_group_id} was not found.")

    dealership_ids = select(Dealership.id).where(Dealership.dealer_group_id == requested_group_id)
    rows = list(
        db.execute(
            select(StockItem, Dealership)
            .join(Dealership, Dealership.id == StockItem.tenant_id)
            .where(StockItem.tenant_id.in_(dealership_ids), StockItem.left_stock_at.is_(None))
            .order_by(StockItem.updated_at.desc())
        ).all()
    )
    return [(item, dealership) for item, dealership in rows]


def get_stock_items_for_vehicles(
    db: Session, *, group_id: uuid.UUID, vehicle_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """KAN-49 / FR-19 amendment — "is this vehicle sitting in the group's
    own stock, and what's its stock item" for a batch of vehicles at once
    (the customer 360's Vehicles tab, one lookup for the whole page).
    Same cross-tenant shape as list_group_stock_items above (Stock is
    dealership-scoped, not group-scoped — this module exists specifically
    for that JOIN on Dealership.dealer_group_id, see the module docstring
    and test_no_ambient_group_read.py's own file allowlist), so it lives
    here rather than in stock_item.py.

    A plain dict per match, never an ORM row — matching
    get_stock_item_pricing's own "plain dict, not an ORM row" posture so
    the caller cannot hold or mutate an inventory object across the
    context boundary.

    No is_authorized parameter, unlike list_group_stock_items: that gate
    is for the bulk group-stock-browsing FEATURE (a role check on top of
    group_read_enabled); this is a passive "is this specific, already-
    known vehicle already in stock" fact on someone else's own screen, so
    only group_read_enabled applies. Returns {} rather than raising when
    the flag is off — the stock-item link is a nicety, not something a
    missing group setting should break the tab over.

    Keyed by vehicle_id; a car can leave and re-enter stock over its life,
    so `left_stock_at IS NULL` (currently in stock) is required — a link
    to a car that has already left is not "currently in the group's own
    stock" — and the newest match wins if more than one somehow qualifies.
    """

    if not vehicle_ids:
        return {}

    group = db.get(DealerGroup, group_id)
    if group is None or not group.group_read_enabled:
        return {}

    dealership_ids = select(Dealership.id).where(Dealership.dealer_group_id == group_id)
    rows = list(
        db.scalars(
            select(StockItem)
            .where(
                StockItem.vehicle_id.in_(vehicle_ids),
                StockItem.tenant_id.in_(dealership_ids),
                StockItem.left_stock_at.is_(None),
            )
            .order_by(StockItem.updated_at.desc())
        ).all()
    )
    by_vehicle: dict[uuid.UUID, dict] = {}
    for item in rows:
        # vehicle_id is nullable on the column (pre-VIN pipeline items,
        # ADR-045) but the .in_(vehicle_ids) filter above already excludes
        # every None row.
        assert item.vehicle_id is not None
        # first seen = newest, due to order_by above
        by_vehicle.setdefault(item.vehicle_id, {"id": item.id, "stockNumber": item.stock_number})
    return by_vehicle
