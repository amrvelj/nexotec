"""Purchase, landed cost, fiktiver Vorsteuerabzug (WP-7 PR-3, Art. 28a
MWSTG). ADR-057: there is NO vatTreatment field anywhere in this module —
notional_input_tax_* is a purchase-side fact belonging to Stock's own
acquisition booking, never shown on a customer document, never read or
written by Sales.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.inventory.models.stock_item import StockItem
from app.inventory.schemas.purchase import NotionalInputTaxOverrideRequest, RecordPurchaseRequest
from app.inventory.services.stock_item import mark_purchased_if_ready
from app.platform.public import get_dealership_or_404


def _compute_notional_input_tax(*, purchase_price: Decimal, rate: Decimal | None) -> Decimal | None:
    """Art. 28a MWSTG: amount = rate / (100 + rate) * purchase price. None
    when the dealership hasn't configured a vat_rate yet — applicable
    stays recorded, the amount is simply not computable until it does.
    """

    if rate is None:
        return None
    return (purchase_price * rate / (Decimal(100) + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def record_purchase(
    db: Session, *, item: StockItem, data: RecordPurchaseRequest, actor_id: uuid.UUID | None
) -> StockItem:
    """Prefills notional_input_tax_applicable from the supplier's own VAT-
    registration status — false for a VAT-registered business (no fiktiver
    Vorsteuerabzug: real input tax was already deducted along the normal
    chain), true for a private individual (the dealer paid no VAT on
    acquisition, so Art. 28a lets it deduct a notional amount instead).
    """

    dealership = get_dealership_or_404(db, item.tenant_id)
    applicable = not data.supplier_is_vat_registered

    item.supplier_name = data.supplier_name
    item.supplier_is_vat_registered = data.supplier_is_vat_registered
    item.purchase_price = data.purchase_price
    item.purchase_date = data.purchase_date
    item.purchase_invoice_ref = data.purchase_invoice_ref
    item.landed_cost = data.landed_cost
    item.notional_input_tax_applicable = applicable
    item.notional_input_tax_rate = dealership.vat_rate if applicable else None
    item.notional_input_tax_amount = (
        _compute_notional_input_tax(purchase_price=data.purchase_price, rate=dealership.vat_rate)
        if applicable
        else None
    )
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    mark_purchased_if_ready(db, item)
    db.commit()
    db.refresh(item)
    return item


def override_notional_input_tax(
    db: Session, *, item: StockItem, data: NotionalInputTaxOverrideRequest, actor_id: uuid.UUID | None
) -> StockItem:
    before = {
        "notionalInputTaxApplicable": item.notional_input_tax_applicable,
        "notionalInputTaxRate": str(item.notional_input_tax_rate) if item.notional_input_tax_rate else None,
        "notionalInputTaxAmount": str(item.notional_input_tax_amount) if item.notional_input_tax_amount else None,
    }

    item.notional_input_tax_applicable = data.applicable
    item.notional_input_tax_rate = data.rate if data.applicable else None
    item.notional_input_tax_amount = (
        _compute_notional_input_tax(purchase_price=item.purchase_price, rate=data.rate)
        if data.applicable and item.purchase_price is not None
        else None
    )
    item.notional_input_tax_overridden = True
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="notional_input_tax_override",
        actor_id=actor_id,
        before=before,
        after={
            "notionalInputTaxApplicable": item.notional_input_tax_applicable,
            "notionalInputTaxRate": str(item.notional_input_tax_rate) if item.notional_input_tax_rate else None,
            "notionalInputTaxAmount": str(item.notional_input_tax_amount) if item.notional_input_tax_amount else None,
        },
        reason=data.reason,
    )
    db.commit()
    db.refresh(item)
    return item
