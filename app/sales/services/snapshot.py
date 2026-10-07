"""ADR-041 — freeze the vehicle specification once, at generation time,
never re-read from live catalogue/stock data afterward (WP-8 PR-3).
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.inventory.public import get_stock_item_pricing
from app.sales.models.line_item import LineItemKind, SalesLineItem
from app.sales.models.offer import SalesOffer
from app.sales.services.pricing import resolve_discount
from app.vehicle.public import HostConfiguration, get_configuration_for_host


@dataclass(frozen=True)
class _SellerEdit:
    """What a seller may have done to a factory-option line (FR-S-07)."""

    included: bool
    discount_type: str | None
    discount_value: Decimal | None
    discount_suppressed_reason: str | None

    @classmethod
    def of(cls, row: SalesLineItem) -> "_SellerEdit":
        return cls(row.included, row.discount_type, row.discount_value, row.discount_suppressed_reason)

    def apply(self, row: SalesLineItem) -> None:
        row.included = self.included
        if self.discount_type is not None and self.discount_value is not None:
            row.discount_type = self.discount_type
            row.discount_value = self.discount_value
            row.discount_suppressed_reason = self.discount_suppressed_reason
            row.discount_resolved_amount = resolve_discount(self.discount_type, self.discount_value, row.unit_price)


def _vehicle_identity(offer: SalesOffer, configuration: HostConfiguration | None) -> str | None:
    """What "the same vehicle" means for re-freeze purposes — a different
    stock item, edited manual details, or (C-F) a different configuration
    or a new version of the same one, are all a genuine vehicle change;
    anything else re-running this function is a no-op.
    """

    if offer.vehicle_source == "stock" and offer.stock_item_id is not None:
        return f"stock:{offer.stock_item_id}"
    if configuration is not None:
        return f"configuration:{configuration.id}:{configuration.version}"
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

    configuration = None
    if offer.vehicle_source == "manual" and offer.configuration_id is not None:
        configuration = get_configuration_for_host(
            db, tenant_id=offer.tenant_id, configuration_id=offer.configuration_id
        )
    identity = _vehicle_identity(offer, configuration)
    if identity is None:
        return False

    existing = offer.vehicle_snapshot or {}
    if offer.vehicle_snapshot_frozen_at is not None and existing.get("_identity") == identity:
        return False

    # C-F (KAN-10): a new version of the SAME configuration (the advisor
    # reopened and saved it) keeps what the seller did to its lines — a
    # deselection or a per-line discount — matched by code. A different
    # configuration or vehicle starts clean.
    kept: dict[tuple[str, str], _SellerEdit] = {}
    if configuration is not None and existing.get("configurationId") == str(configuration.id):
        kept = {
            (row.code, row.label): _SellerEdit.of(row)
            for row in db.scalars(
                select(SalesLineItem).where(
                    SalesLineItem.offer_id == offer.id, SalesLineItem.kind == LineItemKind.FACTORY_OPTION
                )
            ).all()
        }

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
            # KAN-62 — not read by build_up() (which only ever consumes
            # basePrice, already resolved above with the correct fallback
            # for the no-options case); frozen purely so a later reader can
            # see what the stock item's own list/effective price actually
            # were at generation time, distinct from whichever one ended up
            # supplying basePrice.
            "listPrice": str(pricing["listPrice"]) if pricing["listPrice"] is not None else None,
            "effectivePrice": str(pricing["effectivePrice"]) if pricing["effectivePrice"] is not None else None,
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
    elif configuration is not None:
        # C-F (KAN-10, FR-C-12) — Path B through the configurator. The
        # configuration's price lines (selected options, colour and wheels
        # surcharges; FR-C-03) become this offer's factory-option lines,
        # which build_up() sums and the document itemises; the base price
        # was prefilled into manual_base_price when it was attached. The
        # ADR-071 specification block is frozen here — its third carrier.
        # Still no stock item and no known cost (S-D09/ADR-045).
        snapshot = {
            "_identity": identity,
            "vehicleLabel": offer.vehicle_label,
            "condition": offer.manual_vehicle_condition,
            "configurationId": str(configuration.id),
            "configurationVersion": configuration.version,
            "basePrice": None,
            "basePriceYear": configuration.base_price_year,
            "purchasePrice": None,
            "landedCost": None,
            "notionalInputTaxApplicable": None,
            "notionalInputTaxRate": None,
            "notionalInputTaxAmount": None,
            "spec": configuration.spec,
        }
        for position, line in enumerate(configuration.price_lines):
            code, label = line.code or line.kind, line.label[:200]
            row = SalesLineItem(
                tenant_id=offer.tenant_id,
                offer_id=offer.id,
                kind=LineItemKind.FACTORY_OPTION,
                code=code,
                label=label,
                unit_price=line.price,
                quantity=1,
                included=True,
                position=position,
            )
            edit = kept.get((code, label))
            if edit is not None:
                edit.apply(row)
            db.add(row)
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
