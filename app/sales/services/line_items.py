"""SalesLineItem mutation (WP-8 PR-8, S-D14) — "line items are edited via
the offer's own PUT body," one call rather than a per-item CRUD resource
(the plan's own idiom, mirroring how reservation and trade-in each got
their own single action endpoint rather than a generic sub-resource API).

Per-line discounts are individually deselectable on any vehicle, but on a
USED vehicle they are suppressed by default — `discount_suppressed_reason`
is the seller's documented override, required whenever a discount is set
on a used vehicle's line, on EITHER kind (factory option or accessory).
This mirrors app.inventory.services.pricing's own ITEMIZABLE_CONDITIONS
rule (factory options can only ever be itemised on new/tagesz/demo stock
in the first place — a used stock item's frozen snapshot never carries any
factory-option rows to begin with, so "individually deselectable" applies
in practice to new/tagesz/demo vehicles; a used vehicle's accessories are
still a real offer-level collection, hence the reason requirement applying
there too). Flagged as a reasoned interpretation of "factory options
individually deselectable with suppressed-with-reason discounts on used
cars" (the build brief's own phrasing) — worth a live product check.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.schemas.line_item import LineItemsReplaceRequest
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.offer import vehicle_condition
from app.sales.services.pricing import apply_build_up, resolve_discount


def list_line_items(db: Session, *, tenant_id: uuid.UUID, offer_id: uuid.UUID) -> list[SalesLineItem]:
    return list(
        db.scalars(
            select(SalesLineItem)
            .where(SalesLineItem.tenant_id == tenant_id, SalesLineItem.offer_id == offer_id)
            .order_by(SalesLineItem.kind, SalesLineItem.position)
        ).all()
    )


def _is_used_vehicle(offer: SalesOffer) -> bool:
    return vehicle_condition(offer) == "used"


def _apply_discount(
    row: SalesLineItem,
    *,
    discount_type: str | None,
    discount_value: Decimal | None,
    discount_suppressed_reason: str | None,
    used_vehicle: bool,
) -> None:
    wants_discount = discount_type is not None and discount_value is not None
    if wants_discount and used_vehicle and not discount_suppressed_reason:
        raise ConflictError(
            f"A per-line discount on '{row.label}' is suppressed by default on a used vehicle — "
            "supply discountSuppressedReason to override.",
            details={"lineItemId": str(row.id), "code": row.code},
        )
    row.discount_type = discount_type if wants_discount else None
    row.discount_value = discount_value if wants_discount else None
    row.discount_suppressed_reason = discount_suppressed_reason if wants_discount else None
    row.discount_resolved_amount = (
        resolve_discount(discount_type, discount_value, row.unit_price * row.quantity) if wants_discount else None
    )


def replace_line_items(
    db: Session, *, offer: SalesOffer, data: LineItemsReplaceRequest, actor_id: uuid.UUID | None
) -> SalesOffer:
    if offer.status != OfferStatus.DRAFT:
        raise ConflictError(f"Offer {offer.offer_number} can no longer be edited (status '{offer.status.value}').")

    used_vehicle = _is_used_vehicle(offer)
    existing_by_id = {li.id: li for li in list_line_items(db, tenant_id=offer.tenant_id, offer_id=offer.id)}

    # Accessories — a full replace (S-D14: an offer-level collection the
    # seller builds up directly, never system-managed).
    incoming_accessory_ids = {a.id for a in data.accessories if a.id is not None}
    for existing_row in existing_by_id.values():
        if existing_row.kind == LineItemKind.ACCESSORY and existing_row.id not in incoming_accessory_ids:
            db.delete(existing_row)

    for position, accessory in enumerate(data.accessories):
        accessory_row: SalesLineItem
        if accessory.id is not None:
            found = existing_by_id.get(accessory.id)
            if found is None or found.kind != LineItemKind.ACCESSORY:
                raise NotFoundError(f"Accessory line item {accessory.id} was not found.")
            accessory_row = found
        else:
            accessory_row = SalesLineItem(
                tenant_id=offer.tenant_id,
                offer_id=offer.id,
                kind=LineItemKind.ACCESSORY,
                unit_price=accessory.unit_price,
                included=True,
            )
            db.add(accessory_row)
        accessory_row.code = accessory.code
        accessory_row.label = accessory.label
        accessory_row.unit_price = accessory.unit_price
        accessory_row.quantity = accessory.quantity
        accessory_row.position = position
        _apply_discount(
            accessory_row,
            discount_type=accessory.discount_type,
            discount_value=accessory.discount_value,
            discount_suppressed_reason=accessory.discount_suppressed_reason,
            used_vehicle=used_vehicle,
        )

    # Factory options — patch-only, never created/deleted here (the frozen
    # snapshot is the only writer of a factory-option row's existence).
    for patch in data.factory_options:
        option_row = existing_by_id.get(patch.id)
        if option_row is None or option_row.kind != LineItemKind.FACTORY_OPTION:
            raise NotFoundError(f"Factory option line item {patch.id} was not found.")
        option_row.included = patch.included
        _apply_discount(
            option_row,
            discount_type=patch.discount_type,
            discount_value=patch.discount_value,
            discount_suppressed_reason=patch.discount_suppressed_reason,
            used_vehicle=used_vehicle,
        )

    db.flush()

    apply_build_up(db, offer=offer)
    offer.updated_by = actor_id
    offer.version += 1
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer
