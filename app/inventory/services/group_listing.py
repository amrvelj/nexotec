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
