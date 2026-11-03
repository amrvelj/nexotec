"""One-step trade-in (WP-8 PR-5, S-D18/ADR-064): records the trade-in car
on the deal and attaches an existing valid valuation or leaves the seller to
create one — all from a single call, never three separate screens.

**A trade-in never creates a vehicle-mdm record** (Anto, 2026-10-31 /
2026-11-01, KAN-189 options B then A; ADR-070 as amended, FR-S-08 amended).
A car enters the shared register when the Betrieb buys it: the trade-in
becomes a pipeline item at contract confirmation (S-D11/ADR-045), and its
purchase booking creates the record (ADR-103). Until then the deal keeps the
VIN (`trade_in_vin`) and only *links* the record vehicle-mdm already holds
for it (`vehicle.public.match_vehicle`, a read). No owner or keeper party is
allocated (Anto confirmed: the car shows on the customer's Vehicles tab once
it is bought). FR-S-08's "an existing valid valuation is offered before a new
one is made" finds the valuation by the linked record, else by the VIN
(`valuation.public.list_valid_valuations_for_vin`).

KAN-67 (ADR-083): a pending contract records its trade-in through the same
two functions — `deal` is an offer or a contract.
"""

import dataclasses
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.validators import normalise_vin
from app.customer.public import get_customer_or_404
from app.sales.services.configuration_reference import SLOT_TRADE_IN, publish_configuration_attached
from app.sales.services.priced_deal import DealT, PricedDeal, lock_editable, project, require_editable
from app.valuation.public import list_valid_valuations_for_vehicle, list_valid_valuations_for_vin
from app.vehicle.public import match_vehicle


@dataclasses.dataclass(frozen=True)
class _TradeInCar:
    """The trade-in car as the deal keeps it — plain data, never a row: its
    VIN, and the vehicle-mdm record that VIN already names, if any."""

    vin: str
    vehicle_id: uuid.UUID | None


def _resolve_trade_in_car(
    db: Session, *, vin: str | None, plate: str | None, canton: str | None, group_id: uuid.UUID
) -> _TradeInCar:
    if vin:
        normalised = normalise_vin(vin)
        match = match_vehicle(db, vin=normalised, group_id=group_id)
        return _TradeInCar(vin=normalised, vehicle_id=match.vehicle.id if match.vehicle is not None else None)
    if plate:
        # KAN-467: the plate is looked up in the caller's group's register.
        # A plate names a car only through a record that already exists, so
        # it still needs a decisive match — a plate alone is no VIN.
        result = match_vehicle(db, plate=plate, canton=canton, group_id=group_id)
        if result.vehicle is not None and not result.requires_confirmation:
            return _TradeInCar(vin=result.vehicle.vin, vehicle_id=result.vehicle.id)
        raise ConflictError(
            "No decisive match for this plate — a trade-in vehicle needs a VIN "
            "(a plate alone names no car).",
            details={"plate": plate, "canton": canton},
        )
    raise ConflictError("A trade-in vehicle needs either a VIN or a plate+canton to resolve.")


def set_trade_in(
    db: Session,
    *,
    deal: DealT,
    group_id: uuid.UUID,
    vin: str | None,
    plate: str | None,
    canton: str | None,
    vehicle_label: str,
    customer_id: uuid.UUID | None,
    actor_id: uuid.UUID,
) -> DealT:
    require_editable(db, deal)
    version_seen = deal.version

    # KAN-99: the body's customer, if any, is resolved in the caller's group
    # (a customerId from another group is a 404). Since KAN-189 no party is
    # allocated for the trade-in car, so nothing else reads it.
    if customer_id is not None:
        get_customer_or_404(db, group_id, customer_id)

    car = _resolve_trade_in_car(db, vin=vin, plate=plate, canton=canton, group_id=group_id)

    # Every read above runs in the request's transaction and nothing commits
    # it any more (KAN-189 removed the vehicle resolution and the party
    # allocations, KAN-309's reason for re-locking) — the row lock the route
    # took still holds. Re-checked all the same: cheap, and it keeps the
    # version rule in one place.
    lock_editable(db, deal, version_seen=version_seen)

    deal.trade_in_vehicle_id = car.vehicle_id
    deal.trade_in_vin = car.vin
    deal.trade_in_label = vehicle_label

    # FR-S-08: "an existing valid valuation is offered before a new one is
    # made" — auto-attach the newest valid one; a caller with multiple
    # candidates chooses via attach_trade_in_valuation instead of relying on
    # this default. By the linked record when there is one, else by the VIN
    # (a car not yet in the register, KAN-189).
    existing = (
        list_valid_valuations_for_vehicle(db, tenant_id=deal.tenant_id, vehicle_id=car.vehicle_id)
        if car.vehicle_id is not None
        else []
    ) or list_valid_valuations_for_vin(db, tenant_id=deal.tenant_id, vin=car.vin)
    if existing:
        _attach(db, deal, existing[0])

    deal.updated_by = actor_id
    deal.version += 1
    _recompute_payable(deal)
    db.flush()
    project(db, deal)
    db.commit()
    db.refresh(deal)
    return deal


def attach_trade_in_valuation(
    db: Session, *, deal: DealT, valuation_id: uuid.UUID, actor_id: uuid.UUID | None
) -> DealT:
    """Explicit attach — used when the seller picks a specific valuation
    from the list FR-S-08 shows (rather than set_trade_in's own single-
    candidate default), or after creating a brand new one via
    app.valuation.public. A read-only reference (S-D04): only the id and
    final_offer are copied, never the valuation's own inputs/deductibles.

    KAN-67: refused like every other edit once the deal is no longer
    editable — a signed contract's valuation and payable are frozen with
    the rest of it (and its valuation is already stamped used), and an offer
    that left draft is no longer edited either.
    """

    from app.valuation.public import get_valuation_or_404

    require_editable(db, deal)

    valuation = get_valuation_or_404(db, deal.tenant_id, valuation_id)
    _attach(db, deal, valuation)
    deal.updated_by = actor_id
    deal.version += 1
    _recompute_payable(deal)
    db.flush()
    project(db, deal)
    db.commit()
    db.refresh(deal)
    return deal


def _attach(db: Session, deal: PricedDeal, valuation) -> None:
    deal.trade_in_valuation_id = valuation.id
    if valuation.configuration_id is not None:
        if deal.trade_in_configuration_id != valuation.configuration_id:
            # KAN-270: vehicle keeps a configuration a host took for good.
            publish_configuration_attached(
                db, deal=deal, configuration_id=valuation.configuration_id, slot=SLOT_TRADE_IN
            )
        # C-F (KAN-10, FR-C-12 carve-out) — a trade-in captured through the
        # valuation path in `record` mode: the offer carries the
        # valuation's configuration forward, and where no vehicle-mdm record
        # was resolved (ADR-070: the valuation path never writes one), the
        # valuation's own vehicle fields label the trade-in.
        deal.trade_in_configuration_id = valuation.configuration_id
        deal.trade_in_configuration_label_refreshed_at = utcnow()
        if deal.trade_in_vehicle_id is None:
            deal.trade_in_label = valuation.configuration_label or deal.trade_in_label
            deal.trade_in_vin = valuation.vehicle_vin
            deal.trade_in_vehicle_id = valuation.vehicle_id
    deal.trade_in_value = valuation.final_offer
    # Seller-adjustable afterward (S-D04) — defaults to the valuation's own
    # figure, not force-kept in sync with it.
    if deal.trade_in_purchase_price is None:
        deal.trade_in_purchase_price = valuation.final_offer


def _recompute_payable(deal: PricedDeal) -> None:
    if deal.gross_price is None:
        deal.payable = None
        return
    trade_in = deal.trade_in_value or Decimal(0)
    deal.payable = deal.gross_price - trade_in
