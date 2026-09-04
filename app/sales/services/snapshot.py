"""ADR-041 — freeze the vehicle specification once, at generation time,
never re-read from live catalogue/stock data afterward (WP-8 PR-3).
"""

from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.inventory.public import get_stock_item_pricing
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.models.offer import SalesOffer


def _vehicle_identity(offer: SalesOffer) -> str | None:
    """What "the same vehicle" means for re-freeze purposes — a different
    stock item, or edited manual details, are both a genuine vehicle
    change; anything else re-running this function is a no-op.
    """

    if offer.vehicle_source == "stock" and offer.stock_item_id is not None:
        return f"stock:{offer.stock_item_id}"
    if offer.vehicle_source == "manual" and offer.vehicle_label is not None:
        return f"manual:{offer.vehicle_label}:{offer.manual_vehicle_condition}"
    return None


def freeze_vehicle_snapshot(db: Session, *, offer: SalesOffer) -> bool:
    """Idempotent per vehicle identity: freezing twice for the SAME
    vehicle is a no-op (the confirmed reference prototype's own footer,
    verbatim: "a later catalogue correction never changes an existing
    offer"). Re-freezes — dropping and rebuilding the frozen factory-option
    line items — only when the vehicle identity itself changed since the
    last freeze. Returns True iff it froze/re-froze.
    """

    identity = _vehicle_identity(offer)
    if identity is None:
        return False

    existing = offer.vehicle_snapshot or {}
    if offer.vehicle_snapshot_frozen_at is not None and existing.get("_identity") == identity:
        return False

    db.execute(
        delete(SalesLineItem).where(SalesLineItem.offer_id == offer.id, SalesLineItem.kind == LineItemKind.FACTORY_OPTION)
    )

    if offer.vehicle_source == "stock" and offer.stock_item_id is not None:
        pricing = get_stock_item_pricing(db, tenant_id=offer.tenant_id, stock_item_id=offer.stock_item_id)
        snapshot = {
            "_identity": identity,
            "vehicleLabel": offer.vehicle_label,
            "condition": pricing["condition"],
            "basePrice": str(pricing["basePrice"]) if pricing["basePrice"] is not None else None,
            "purchasePrice": str(pricing["purchasePrice"]) if pricing["purchasePrice"] is not None else None,
            # KAN-25: frozen alongside purchasePrice, same ADR-041 posture
            # — a later purchase-booking correction never changes an
            # already-generated offer. notionalInputTaxAmount stays a
            # positive credit here too; build_up() is where it gets
            # subtracted.
            "landedCost": str(pricing["landedCost"]) if pricing["landedCost"] is not None else None,
            "notionalInputTaxApplicable": pricing["notionalInputTaxApplicable"],
            "notionalInputTaxRate": (
                str(pricing["notionalInputTaxRate"]) if pricing["notionalInputTaxRate"] is not None else None
            ),
            "notionalInputTaxAmount": (
                str(pricing["notionalInputTaxAmount"]) if pricing["notionalInputTaxAmount"] is not None else None
            ),
        }
        for position, option in enumerate(pricing["options"]):
            db.add(
                SalesLineItem(
                    tenant_id=offer.tenant_id,
                    offer_id=offer.id,
                    kind=LineItemKind.FACTORY_OPTION,
                    code=option["code"],
                    label=option["label"],
                    unit_price=Decimal(option["price"]),
                    quantity=1,
                    included=True,
                    position=position,
                )
            )
    else:
        # Manual configuration — no stock item, no known cost, no options
        # to itemize (S-D09/ADR-045: this never touches inventory).
        snapshot = {
            "_identity": identity,
            "vehicleLabel": offer.vehicle_label,
            "condition": offer.manual_vehicle_condition,
            "basePrice": None,
            "purchasePrice": None,
            "landedCost": None,
            "notionalInputTaxApplicable": None,
            "notionalInputTaxRate": None,
            "notionalInputTaxAmount": None,
        }

    offer.vehicle_snapshot = snapshot
    offer.vehicle_snapshot_frozen_at = utcnow()
    db.flush()
    return True
