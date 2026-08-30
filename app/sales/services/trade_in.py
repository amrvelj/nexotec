"""One-step trade-in (WP-8 PR-5, S-D18/ADR-064): records the vehicle,
allocates the customer as its party, and attaches an existing valid
valuation or leaves the seller to create one — all from a single call,
never three separate screens.

Sales never owns the traded-in vehicle (S-D11/ADR-045) — it is a real
`VehicleMdm` row, created through `app.vehicle.public`, exactly like any
other vehicle. The pipeline STOCK ITEM this trade-in eventually becomes is
a separate, later moment: contract confirmation (PR-6), not here.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.customer.public import VehiclePartyRole, allocate_vehicle_party
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from app.valuation.public import list_valid_valuations_for_vehicle
from app.vehicle.public import create_or_get_vehicle_mdm, match_vehicle


def _resolve_trade_in_vin(db: Session, *, vin: str | None, plate: str | None, canton: str | None) -> str:
    if vin:
        return vin
    if plate:
        result = match_vehicle(db, plate=plate, canton=canton)
        if result.vehicle is not None and not result.requires_confirmation:
            return result.vehicle.vin
        raise ConflictError(
            "No decisive match for this plate — a trade-in vehicle needs a VIN "
            "(vehicle-mdm requires one; a plate alone cannot create a record).",
            details={"plate": plate, "canton": canton},
        )
    raise ConflictError("A trade-in vehicle needs either a VIN or a plate+canton to resolve.")


def set_trade_in(
    db: Session,
    *,
    offer: SalesOffer,
    group_id: uuid.UUID,
    vin: str | None,
    plate: str | None,
    canton: str | None,
    vehicle_label: str,
    customer_id: uuid.UUID | None,
    actor_id: uuid.UUID,
) -> SalesOffer:
    if offer.status != OfferStatus.DRAFT:
        raise ConflictError(f"Offer {offer.offer_number} can no longer be edited (status '{offer.status.value}').")

    resolved_vin = _resolve_trade_in_vin(db, vin=vin, plate=plate, canton=canton)
    vehicle, _created = create_or_get_vehicle_mdm(db, vin=resolved_vin)

    # FR-S-08: the customer's own vehicles are offered first by party role
    # — allocate as BOTH owner and keeper (one role per call, ADR-064).
    party_customer_id = customer_id or offer.customer_id
    if party_customer_id is not None:
        allocate_vehicle_party(
            db, vehicle_id=vehicle.id, customer_id=party_customer_id, role=VehiclePartyRole.OWNER,
            group_id=group_id, actor_id=actor_id,
        )
        allocate_vehicle_party(
            db, vehicle_id=vehicle.id, customer_id=party_customer_id, role=VehiclePartyRole.KEEPER,
            group_id=group_id, actor_id=actor_id,
        )

    offer.trade_in_vehicle_id = vehicle.id
    offer.trade_in_vin = vehicle.vin
    offer.trade_in_label = vehicle_label

    # FR-S-08: "an existing valid valuation is offered before a new one is
    # made" — auto-attach the newest valid one if there is exactly the
    # unambiguous case; a caller with multiple candidates chooses via
    # attach_trade_in_valuation instead of relying on this default.
    existing = list_valid_valuations_for_vehicle(db, tenant_id=offer.tenant_id, vehicle_id=vehicle.id)
    if existing:
        _attach(offer, existing[0])

    offer.updated_by = actor_id
    offer.version += 1
    _recompute_payable(offer)
    db.flush()
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def attach_trade_in_valuation(
    db: Session, *, offer: SalesOffer, valuation_id: uuid.UUID, actor_id: uuid.UUID | None
) -> SalesOffer:
    """Explicit attach — used when the seller picks a specific valuation
    from the list FR-S-08 shows (rather than set_trade_in's own single-
    candidate default), or after creating a brand new one via
    app.valuation.public. A read-only reference (S-D04): only the id and
    final_offer are copied, never the valuation's own inputs/deductibles.
    """

    from app.valuation.public import get_valuation_or_404

    valuation = get_valuation_or_404(db, offer.tenant_id, valuation_id)
    _attach(offer, valuation)
    offer.updated_by = actor_id
    offer.version += 1
    _recompute_payable(offer)
    db.flush()
    upsert_deal_projection(db, offer=offer)
    db.commit()
    db.refresh(offer)
    return offer


def _attach(offer: SalesOffer, valuation) -> None:
    offer.trade_in_valuation_id = valuation.id
    offer.trade_in_value = valuation.final_offer
    # Seller-adjustable afterward (S-D04) — defaults to the valuation's own
    # figure, not force-kept in sync with it.
    if offer.trade_in_purchase_price is None:
        offer.trade_in_purchase_price = valuation.final_offer


def _recompute_payable(offer: SalesOffer) -> None:
    if offer.gross_price is None:
        offer.payable = None
        return
    trade_in = offer.trade_in_value or Decimal(0)
    offer.payable = offer.gross_price - trade_in
