"""The invoicing gate (WP-7 PR-5, ADR-052). `is_invoiceable` is a
REPLICATED FACT — Stock is the sole source of truth for it, published via
`inventory.stock_item.purchased` (services/stock_item.py::
mark_purchased_if_ready, PR-3) so a future Sales can set its own local
`contract.isInvoiceable` copy with no synchronous hop. This module is the
other half: Stock re-asserting the invariant when `finance.invoice.issued`
actually happens.

No app.finance exists yet, so `apply_finance_invoice_issued` is called
directly (by a future Finance consumer, once one exists) rather than
wired to a real outbox event today — same forward-compatible-
infrastructure posture as PR-2's sales.contract.confirmed consumer.

FR-I-12: on a legitimate invoice against an in_stock, invoiceable item,
the item leaves the active list (left_stock_at set, inventory.
stock_item.sold emitted) — never a 4th lifecycle_status value. On an
invoice against anything else (not in_stock, or in_stock but never
marked invoiceable), that's a genuine integrity violation — the
dealership's own gate (S-D10) should have refused the contract
confirmation before Finance ever got here — so it's recorded as an audit
event and raised as a ConflictError, never silently accepted.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import LifecycleStatus, StockItem
from app.inventory.services.stock_item import get_stock_item_or_404

_EVENT_PRODUCER = "inventory"


def apply_finance_invoice_issued(
    db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID, invoice_ref: str
) -> StockItem:
    item = get_stock_item_or_404(db, tenant_id, stock_item_id)

    if item.left_stock_at is not None:
        return item  # already sold — redelivery/retry, not a second event

    if item.lifecycle_status != LifecycleStatus.IN_STOCK or not item.is_invoiceable:
        record_audit_event(
            db,
            entity_type="stock_item",
            entity_id=item.id,
            tenant_id=item.tenant_id,
            action="invoicing_gate_alarm",
            actor_id=None,
            before={
                "lifecycleStatus": item.lifecycle_status.value,
                "isInvoiceable": item.is_invoiceable,
            },
            reason=f"finance.invoice.issued ({invoice_ref}) against a stock item that was not in_stock+invoiceable.",
        )
        db.commit()
        raise ConflictError(
            f"Stock item {stock_item_id} received an invoice but is not in_stock+invoiceable — "
            "the confirmation gate (S-D10) should have refused this earlier.",
            details={"stockItemId": str(stock_item_id), "invoiceRef": invoice_ref},
        )

    item.left_stock_at = utcnow()
    item.version += 1
    db.flush()

    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.sold",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={"invoiceRef": invoice_ref},
        ),
    )
    db.commit()
    db.refresh(item)
    return item
