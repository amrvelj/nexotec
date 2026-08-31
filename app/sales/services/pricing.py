"""pricing.build_up() (WP-8 PR-3) — the single pure(-ish) server-side
derivation, base -> options -> list -> accessories -> total -> discount ->
price (FR-S's own level order), read from the FROZEN snapshot and the
line-item table, never from live stock data (ADR-041).
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.models.offer import SalesOffer

_CENTS = Decimal("0.01")


def resolve_discount(discount_type: str | None, discount_value: Decimal | None, base_amount: Decimal) -> Decimal:
    """{type, value} -> resolvedAmount (FR-S's own discount shape). No
    discount configured is exactly `amount=0`, never None, so callers
    never have to special-case "no discount" separately from "0 discount".
    """

    if discount_type is None or discount_value is None:
        return Decimal(0)
    if discount_type == "percent":
        return (base_amount * discount_value / Decimal(100)).quantize(_CENTS)
    return discount_value


def build_up(db: Session, *, offer: SalesOffer) -> dict:
    # A manual configuration's price is a plain mutable field the seller
    # types directly (no live catalogue source to freeze against, unlike
    # a stock vehicle) — see app/sales/models/offer.py's own comment on
    # manual_base_price. A stock vehicle's price comes from the FROZEN
    # snapshot only, never live stock data (ADR-041).
    if offer.vehicle_source == "manual":
        base_price = offer.manual_base_price or Decimal(0)
        cost_basis = None
    else:
        snapshot = offer.vehicle_snapshot or {}
        base_price = Decimal(snapshot["basePrice"]) if snapshot.get("basePrice") is not None else Decimal(0)
        cost_basis = Decimal(snapshot["purchasePrice"]) if snapshot.get("purchasePrice") is not None else None

    line_items = list(db.scalars(select(SalesLineItem).where(SalesLineItem.offer_id == offer.id)).all())

    def _line_total(kind: LineItemKind) -> Decimal:
        return sum(
            (
                (li.unit_price * li.quantity) - (li.discount_resolved_amount or Decimal(0))
                for li in line_items
                if li.kind == kind and li.included
            ),
            Decimal(0),
        )

    options_total = _line_total(LineItemKind.FACTORY_OPTION)
    accessories_total = _line_total(LineItemKind.ACCESSORY)

    list_price = base_price + options_total
    total_before_discount = list_price + accessories_total
    discount_amount = resolve_discount(offer.discount_type, offer.discount_value, total_before_discount)
    gross_price = total_before_discount - discount_amount

    margin = (gross_price - cost_basis) if cost_basis is not None else None

    return {
        "basePrice": base_price,
        "optionsTotal": options_total,
        "listPrice": list_price,
        "accessoriesTotal": accessories_total,
        "totalBeforeDiscount": total_before_discount,
        "discountAmount": discount_amount,
        "grossPrice": gross_price,
        "costBasis": cost_basis,
        "margin": margin,
    }


def apply_build_up(db: Session, *, offer: SalesOffer) -> None:
    """Materializes build_up()'s result onto the offer's own columns —
    what the grid and this row itself display. Does not commit; the
    caller (update_offer) owns the transaction.
    """

    result = build_up(db, offer=offer)
    offer.base_price = result["basePrice"]
    offer.options_total = result["optionsTotal"]
    offer.list_price = result["listPrice"]
    offer.accessories_total = result["accessoriesTotal"]
    offer.total_before_discount = result["totalBeforeDiscount"]
    offer.discount_amount = result["discountAmount"]
    offer.gross_price = result["grossPrice"]
    offer.cost_basis = result["costBasis"]
    offer.margin = result["margin"]
    # WP-8 PR-5 — "Zu bezahlen" (confirmed live), recomputed here too so a
    # pricing change (a new discount) keeps payable in sync without the
    # trade-in container needing to be touched again.
    offer.payable = offer.gross_price - (offer.trade_in_value or Decimal(0))
