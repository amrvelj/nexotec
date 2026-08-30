"""SalesContract service layer (WP-8 PR-1). Lifecycle transitions
(pending -> confirmed, the reservation call, the two distinct events) are
PR-6 — this ships the entity, its two creation paths (from an offer, or
directly), and cancellation while still pending.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.numbering import allocate_contract_number

_EVENT_PRODUCER = "sales"


def get_contract_or_404(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> SalesContract:
    contract = db.scalar(
        select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == tenant_id)
    )
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} was not found.")
    return contract


def create_contract(
    db: Session, *, tenant_id: uuid.UUID, offer: SalesOffer | None, actor_id: uuid.UUID | None
) -> SalesContract:
    """`offer=None` is the direct "Vertrag erstellen" path (confirmed live
    as a stock item's own primary detail-header action); `offer` set is
    "Vertrag erzeugen" from an existing offer's row menu, which denormalizes
    the offer's number as lineage and copies its working fields across —
    the confirmed reference prototype's own "C-001195 ← O-003216" header.
    """

    contract = SalesContract(
        tenant_id=tenant_id,
        contract_number=allocate_contract_number(db, tenant_id),
        offer_id=offer.id if offer is not None else None,
        offer_number=offer.offer_number if offer is not None else None,
        customer_id=offer.customer_id if offer is not None else None,
        customer_label=offer.customer_label if offer is not None else None,
        customer_locality=offer.customer_locality if offer is not None else None,
        customer_denorm_refreshed_at=offer.customer_denorm_refreshed_at if offer is not None else None,
        stock_item_id=offer.stock_item_id if offer is not None else None,
        vehicle_label=offer.vehicle_label if offer is not None else None,
        gross_price=offer.gross_price if offer is not None else None,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(contract)
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.created",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={
                "contractNumber": contract.contract_number,
                "offerId": str(offer.id) if offer is not None else None,
                "customerId": str(contract.customer_id) if contract.customer_id is not None else None,
            },
        ),
    )
    upsert_deal_projection(db, contract=contract)
    db.commit()
    db.refresh(contract)
    return contract


def cancel_contract(db: Session, *, contract: SalesContract, reason: str, actor_id: uuid.UUID | None) -> SalesContract:
    """Cancellation from CONFIRMED (which must also release the stock
    reservation) is PR-6 scope — this ships the PENDING-only path, since
    reserve()/release() do not exist on this side of the codebase yet.
    """

    if contract.status != ContractStatus.PENDING:
        raise ConflictError(
            f"Contract {contract.contract_number} cannot be cancelled from status '{contract.status.value}'.",
            details={"status": contract.status.value},
        )

    contract.status = ContractStatus.CANCELLED
    contract.cancelled_reason = reason
    contract.updated_by = actor_id
    contract.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.cancelled",
            tenant_id=contract.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={"contractNumber": contract.contract_number, "reason": reason},
        ),
    )
    upsert_deal_projection(db, contract=contract)
    db.commit()
    db.refresh(contract)
    return contract


def list_contracts(
    db: Session, *, tenant_id: uuid.UUID, params: SortPageParams
) -> tuple[list[SalesContract], str | None, int, bool]:
    stmt = select(SalesContract).where(SalesContract.tenant_id == tenant_id)
    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=SalesContract, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate
