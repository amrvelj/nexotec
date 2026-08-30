"""SalesContract service layer. Creation (PR-1) and confirmation/lifecycle
(PR-6: pending -> confirmed, the reservation call via a dedicated
session — ADR-047 Pattern B — and the two distinct events) live together
here.
"""

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import CustomerLifecycleStatus, get_customer_or_404
from app.db import SessionLocal
from app.inventory.public import release, reserve
from app.sales.models.contract import ContractStatus, FinancingKind, SalesContract
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
        vehicle_source=offer.vehicle_source if offer is not None else None,
        stock_item_id=offer.stock_item_id if offer is not None else None,
        vehicle_label=offer.vehicle_label if offer is not None else None,
        manual_vehicle_condition=offer.manual_vehicle_condition if offer is not None else None,
        gross_price=offer.gross_price if offer is not None else None,
        margin=offer.margin if offer is not None else None,
        trade_in_vehicle_id=offer.trade_in_vehicle_id if offer is not None else None,
        trade_in_label=offer.trade_in_label if offer is not None else None,
        trade_in_vin=offer.trade_in_vin if offer is not None else None,
        trade_in_valuation_id=offer.trade_in_valuation_id if offer is not None else None,
        trade_in_value=offer.trade_in_value if offer is not None else None,
        trade_in_purchase_price=offer.trade_in_purchase_price if offer is not None else None,
        payable=offer.payable if offer is not None else None,
        financing=(
            FinancingKind.LEASING
            if offer is not None and offer.leasing_term_months is not None
            else FinancingKind.CASH
        ),
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


def _confirmed_event_payload(contract: SalesContract) -> dict:
    """The EXACT shape app.inventory.services.pipeline::
    handle_sales_contract_confirmed already reads (WP-7, frozen) —
    "existing"/"manual" (not "stock"/"manual", SalesContract's own
    vocabulary) is the one translation this function exists to make.
    """

    manual_configuration = None
    if contract.vehicle_source == "manual":
        manual_configuration = {"vehicleLabel": contract.vehicle_label, "condition": contract.manual_vehicle_condition}

    trade_in = None
    if contract.trade_in_vehicle_id is not None:
        # Trade-ins are always a used car by definition — there is no
        # separate condition concept on the trade-in side to carry here.
        trade_in = {"vehicleLabel": contract.trade_in_label, "condition": "used"}

    return {
        "contractId": str(contract.id),
        "vehicleSource": "existing" if contract.vehicle_source == "stock" else "manual",
        "manualConfiguration": manual_configuration,
        "tradeIn": trade_in,
    }


def confirm_contract(
    db: Session,
    *,
    contract: SalesContract,
    group_id: uuid.UUID,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session] = SessionLocal,
) -> SalesContract:
    """The core of PR-6. Guards (ADR-065/S-D19), then — for a "stock"
    vehicle source only — reserve() on a DEDICATED SHORT-LIVED SESSION
    (ADR-047 Pattern B): reserve() ends in its own commit, and passing the
    request session while holding this function's own uncommitted writes
    would sweep them in on the ordinary path and silently violate the rule
    on any other. If this function's own transaction then fails, the
    reservation is released as a compensating action (never rolled back
    together with it — that would be the shared-transaction anti-pattern
    ADR-047 exists to forbid).

    `session_factory` defaults to the real `app.db.SessionLocal` (bound to
    the app's own configured database) — overridden only by tests, which
    run against a separate test-only engine and therefore need their own
    session factory rather than the production one.
    """

    if contract.status != ContractStatus.PENDING:
        raise ConflictError(
            f"Contract {contract.contract_number} cannot be confirmed from status '{contract.status.value}'.",
            details={"status": contract.status.value},
        )

    if contract.customer_id is not None:
        customer = get_customer_or_404(db, group_id, contract.customer_id)
        if customer.lifecycle_status == CustomerLifecycleStatus.DO_NOT_CONTACT:
            raise ConflictError(
                f"Customer is do-not-contact — contract {contract.contract_number} cannot be confirmed."
            )
        if customer.credit_block:
            raise ConflictError(
                f"Customer has a credit block ({customer.credit_block_reason}) — contract "
                f"{contract.contract_number} cannot be confirmed.",
                details={"creditBlockReason": customer.credit_block_reason},
            )

    reservation_id: uuid.UUID | None = None
    if contract.vehicle_source == "stock" and contract.stock_item_id is not None:
        short_lived = session_factory()
        try:
            result = reserve(
                short_lived,
                tenant_id=contract.tenant_id,
                stock_item_id=contract.stock_item_id,
                contract_id=contract.id,
                idempotency_key=f"sales.contract.confirm:{contract.id}",
            )
        finally:
            short_lived.close()
        reservation_id = uuid.UUID(result["reservationId"])

    try:
        contract.status = ContractStatus.CONFIRMED
        contract.signed_at = utcnow()
        contract.reservation_id = reservation_id
        contract.updated_by = actor_id
        contract.version += 1
        db.flush()

        publish(
            db,
            OutboxEvent(
                event_type="sales.contract.confirmed",
                tenant_id=contract.tenant_id,
                producer=_EVENT_PRODUCER,
                aggregate_type="sales_contract",
                aggregate_id=contract.id,
                payload=_confirmed_event_payload(contract),
            ),
        )
        upsert_deal_projection(db, contract=contract)
        db.commit()
    except Exception:
        db.rollback()
        if reservation_id is not None:
            compensating = session_factory()
            try:
                release(
                    compensating,
                    tenant_id=contract.tenant_id,
                    reservation_id=reservation_id,
                    idempotency_key=f"sales.contract.confirm-compensate:{contract.id}",
                )
            finally:
                compensating.close()
        raise

    db.refresh(contract)
    return contract


def request_invoice(db: Session, *, contract: SalesContract, actor_id: uuid.UUID | None) -> SalesContract:
    """ADR-046 — a genuinely distinct event from `sales.contract.confirmed`,
    never the same name for both moments. Emitted at hand-off, not at
    signature; does not itself flip is_invoiceable (that is the local
    replica the inventory.stock_item.purchased consumer maintains) or the
    contract's own status (INVOICED is finance's own trigger, WP-9+).
    """

    if contract.status != ContractStatus.CONFIRMED:
        raise ConflictError(
            f"Contract {contract.contract_number} cannot request invoicing from status "
            f"'{contract.status.value}'."
        )

    publish(
        db,
        OutboxEvent(
            event_type="sales.contract.invoice_requested",
            tenant_id=contract.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="sales_contract",
            aggregate_id=contract.id,
            payload={
                "contractNumber": contract.contract_number,
                "stockItemId": str(contract.stock_item_id) if contract.stock_item_id else None,
                "grossPrice": str(contract.gross_price) if contract.gross_price is not None else None,
                "deliveryDate": contract.delivery_date.isoformat() if contract.delivery_date else None,
            },
        ),
    )
    db.commit()
    db.refresh(contract)
    return contract


def cancel_contract(
    db: Session,
    *,
    contract: SalesContract,
    reason: str,
    actor_id: uuid.UUID | None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> SalesContract:
    """PENDING or CONFIRMED can both be cancelled — CONFIRMED additionally
    releases the stock reservation first (Pattern B, dedicated session,
    same reasoning as confirm_contract's own reserve() call).
    """

    if contract.status not in (ContractStatus.PENDING, ContractStatus.CONFIRMED):
        raise ConflictError(
            f"Contract {contract.contract_number} cannot be cancelled from status '{contract.status.value}'.",
            details={"status": contract.status.value},
        )

    if contract.status == ContractStatus.CONFIRMED and contract.reservation_id is not None:
        short_lived = session_factory()
        try:
            release(
                short_lived,
                tenant_id=contract.tenant_id,
                reservation_id=contract.reservation_id,
                idempotency_key=f"sales.contract.cancel:{contract.id}",
            )
        finally:
            short_lived.close()

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
