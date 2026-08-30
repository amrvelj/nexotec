"""Factory options — one list, two consumers (WP-7 PR-9, FR-I-22)."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.models.stock_item_option import StockItemOption
from app.inventory.schemas.pricing import OptionInput
from app.inventory.services.stock_item import get_stock_item_or_404

# Itemised only where condition is new/tagesz/demo — never a used car
# (avoids inviting per-line discount negotiation on a used-car deal).
ITEMIZABLE_CONDITIONS = frozenset(
    {StockItemCondition.NEW, StockItemCondition.TAGESZ, StockItemCondition.DEMO}
)


def list_options(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> list[StockItemOption]:
    return list(
        db.scalars(
            select(StockItemOption).where(
                StockItemOption.tenant_id == tenant_id, StockItemOption.stock_item_id == stock_item_id
            )
        ).all()
    )


def set_options(
    db: Session, *, item: StockItem, base_price: Decimal, options: list[OptionInput], actor_id: uuid.UUID | None
) -> StockItem:
    if options and item.condition not in ITEMIZABLE_CONDITIONS:
        raise ConflictError(
            f"Factory options may only be itemised on new/tagesz/demo stock, not '{item.condition.value}'.",
            details={"stockItemId": str(item.id), "condition": item.condition.value},
        )

    existing = list_options(db, tenant_id=item.tenant_id, stock_item_id=item.id)
    for row in existing:
        db.delete(row)
    db.flush()

    for option in options:
        db.add(
            StockItemOption(
                tenant_id=item.tenant_id,
                stock_item_id=item.id,
                code=option.code,
                label=option.label,
                price=option.price,
                equipment_code=option.equipment_code,
            )
        )

    item.base_price = base_price
    # listPrice = basePrice + Σ options[].price when options exist — the
    # plain typed-value listPrice stands alone for the ordinary used-car
    # case (FR-I-22).
    item.list_price = base_price + sum((o.price for o in options), Decimal(0))
    item.updated_by = actor_id
    item.version += 1
    db.commit()
    db.refresh(item)
    return item


def marketplace_equipment_codes(options: list[StockItemOption]) -> list[str]:
    """The other consumer of the same list (ADR-062) — a car cannot be
    advertised with equipment it is not priced with."""

    return [o.equipment_code for o in options if o.equipment_code is not None]


def get_stock_item_pricing(db: Session, *, tenant_id: uuid.UUID, stock_item_id: uuid.UUID) -> dict:
    """WP-8 PR-3 — the read surface `app.inventory.public.get_stock_item_pricing`
    exposes to Sales for its own offer pricing build-up (ADR-041's frozen
    snapshot is taken from exactly this dict, once, at freeze time — never
    a live ORM object, matching app.vehicle.public.get_vehicle_equipment's
    own "plain dict, not an ORM row" posture so Sales cannot hold or
    mutate an inventory object).

    `purchasePrice` (Einstandspreis) is the one field here Sales must
    never surface to a customer — it feeds only the seller-only margin
    calculation (ADR-049/029), never the printed price build-up.
    """

    item = get_stock_item_or_404(db, tenant_id, stock_item_id)
    options = list_options(db, tenant_id=tenant_id, stock_item_id=stock_item_id)
    return {
        "basePrice": item.base_price,
        "listPrice": item.list_price,
        "effectivePrice": item.effective_price,
        "purchasePrice": item.purchase_price,
        "condition": item.condition.value,
        "options": [{"code": o.code, "label": o.label, "price": o.price} for o in options],
    }
