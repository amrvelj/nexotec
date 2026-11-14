"""The public stock projection against Stock's website listings (KAN-225;
rule 10, ADR-107). Read-only — see app.core.reconciliation for the
mechanism; a finding is reported, never repaired (Stock restates a listing
on its next change, and a dead-lettered statement alerts on its own).

- a projection row the website shows whose stock item Stock no longer lists
  there (or never did), unchanged for an hour;
- a stock item Stock lists on the website with no projection row showing it,
  its listing unchanged for an hour (the statement was not consumed);
- every projection row names a Betrieb that exists.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.inventory.public import listed_on_website_check, website_listing_exists
from app.platform.public import Dealership
from app.reporting.models.public_stock_listing import PublicStockListing

CONTEXT = "reporting"

# How long a statement may be in flight on the outbox.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)

CHECKS: list[ReferenceCheck | StateCheck] = [
    StateCheck(
        label="public_stock_listing shows a car Stock does not list on the website",
        source_model=PublicStockListing,
        source_row_id_column=PublicStockListing.id,
        where=lambda: PublicStockListing.is_listed.is_(True)
        & (PublicStockListing.updated_at < utcnow() - _OUTBOX_LAG_GRACE)
        & ~website_listing_exists(PublicStockListing.stock_item_id, tenant_id=PublicStockListing.tenant_id).correlate(
            PublicStockListing
        ),
    ),
    listed_on_website_check(
        label="stock item listed on the website with no public stock row showing it",
        grace=_OUTBOX_LAG_GRACE,
        projection=lambda stock_item_id, tenant_id: select(PublicStockListing.id).where(
            PublicStockListing.stock_item_id == stock_item_id,
            PublicStockListing.tenant_id == tenant_id,
            PublicStockListing.is_listed.is_(True),
        ),
    ),
    ReferenceCheck(
        label="public_stock_listing.tenant_id -> dealership.id",
        source_model=PublicStockListing,
        source_row_id_column=PublicStockListing.id,
        source_fk_column=PublicStockListing.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)
