"""The overview grid's own read service (WP-8 PR-1) — GET /v1/sales/deals,
"Offerten & Verträge" in the reference prototype. One shared STATUS column
spans both an offer's vocabulary (draft/open/cancelled) and a contract's
(pending/confirmed/cancelled/invoiced), which is exactly why `sales_deal`
stores status as a plain string rather than either entity's own enum type.
"""

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.sales.models.deal import SalesDeal

# An offer that's never been opened for real work — nothing a seller has
# started, nothing worth a row in the overview. ContractStatus has no
# equivalent "draft" value (a contract starts at PENDING, which DOES show
# — an aborted-mid-flow or not-yet-confirmed contract is real, in-progress
# work, unlike a bare draft offer). Never filtered out of the underlying
# sales_deal table itself — upsert_deal_projection still writes the row,
# so the "one row per lineage" identity survives the draft->open (or
# draft offer -> confirmed contract) transition; only this read excludes it.
_EXCLUDED_STATUS = "draft"
_EXCLUDED_ENTITY_TYPE = "offer"


def list_deals(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    q: str | None,
    entity_type: str | None,
    params: SortPageParams,
    owner_user_id: uuid.UUID | None = None,
    unassigned: bool = False,
) -> tuple[list[SalesDeal], str | None, int, bool]:
    """`owner_user_id` narrows to one seller's deals; `unassigned` to the
    queue FR-A-37 names — OPEN deals (an open offer, a pending contract) with
    no responsible seller, e.g. a leaver's handed to nobody (KAN-200). Both
    together is an empty page, not an error."""

    stmt = select(SalesDeal).where(
        SalesDeal.tenant_id == tenant_id,
        ~and_(SalesDeal.entity_type == _EXCLUDED_ENTITY_TYPE, SalesDeal.status == _EXCLUDED_STATUS),
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SalesDeal.number.ilike(like),
                SalesDeal.customer_label.ilike(like),
                SalesDeal.vehicle_label.ilike(like),
            )
        )
    if entity_type is not None:
        stmt = stmt.where(SalesDeal.entity_type == entity_type)
    if owner_user_id is not None:
        stmt = stmt.where(SalesDeal.owner_user_id == owner_user_id)
    if unassigned:
        # FR-A-37's queue: OPEN records nobody is responsible for — an open
        # offer or a pending contract. A signed, invoiced or cancelled deal
        # without an owner (a legacy sale, a long-gone creator) is history.
        stmt = stmt.where(
            SalesDeal.owner_user_id.is_(None),
            or_(
                and_(SalesDeal.entity_type == "offer", SalesDeal.status.in_(("draft", "open"))),
                and_(SalesDeal.entity_type == "contract", SalesDeal.status == "pending"),
            ),
        )

    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesDeal, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
