"""SalesOffer service layer (WP-8 PR-1). Creation and the container-based
autosave/completeness mechanics are split deliberately: PR-1 ships the bare
entity + grid; PR-2 adds `update_offer`'s container-completeness contract.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_offer_number

_EVENT_PRODUCER = "sales"


def get_offer_or_404(db: Session, tenant_id: uuid.UUID, offer_id: uuid.UUID) -> SalesOffer:
    offer = db.scalar(select(SalesOffer).where(SalesOffer.id == offer_id, SalesOffer.tenant_id == tenant_id))
    if offer is None:
        raise NotFoundError(f"Offer {offer_id} was not found.")
    return offer


def create_offer(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None) -> SalesOffer:
    offer = SalesOffer(
        tenant_id=tenant_id,
        offer_number=allocate_offer_number(db, tenant_id),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(offer)
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.offer.created",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_offer",
            aggregate_id=offer.id,
            payload={"offerNumber": offer.offer_number},
        ),
    )
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def cancel_offer(db: Session, *, offer: SalesOffer, reason: str, actor_id: uuid.UUID | None) -> SalesOffer:
    if offer.status == OfferStatus.CANCELLED:
        raise ConflictError(f"Offer {offer.offer_number} is already cancelled.")

    offer.status = OfferStatus.CANCELLED
    offer.cancelled_reason = reason
    offer.updated_by = actor_id
    offer.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.offer.cancelled",
            tenant_id=offer.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_offer",
            aggregate_id=offer.id,
            payload={"offerNumber": offer.offer_number, "reason": reason},
        ),
    )
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def list_offers(
    db: Session, *, tenant_id: uuid.UUID, params: SortPageParams
) -> tuple[list[SalesOffer], str | None, int, bool]:
    stmt = select(SalesOffer).where(SalesOffer.tenant_id == tenant_id)
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesOffer, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
