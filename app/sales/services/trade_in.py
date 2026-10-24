"""One-step trade-in (WP-8 PR-5, S-D18/ADR-064): records the vehicle,
allocates the customer as its party, and attaches an existing valid
valuation or leaves the seller to create one — all from a single call,
never three separate screens.

Sales never owns the traded-in vehicle (S-D11/ADR-045) — it is a real
`VehicleMdm` row, created through `app.vehicle.public`, exactly like any
other vehicle. The pipeline STOCK ITEM this trade-in eventually becomes is
a separate, later moment: contract confirmation (PR-6), not here.

KAN-67 (ADR-083): a pending contract records its trade-in through the same
two functions — `deal` is an offer or a contract.

**The vehicle is resolved on a session of its own** (KAN-328, as Stock's
`inventory/services/vehicle_resolution.py` does since KAN-257).
`create_or_get_vehicle_mdm` commits its own transaction (ADR-047) and, on a
lost insert race, its guard rolls back the session it is handed — handed
the request session, the first would commit whatever the request had
pending and the second would silently discard it. So `_resolve_vehicle`
gives vehicle-mdm a short-lived session bound to the same database and
takes back plain values. A lost race (two writers resolving a VIN
vehicle-mdm has never seen) is vehicle-mdm's 409 naming the VIN; the loser
looks again and goes on with the winner's record — FR-V-15: same VIN, same
car — instead of answering 409. The record names the user whose trade-in
created it (KAN-243: a null `created_by` means a system write).
"""

import dataclasses
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.errors import ConflictError
from app.customer.public import VehiclePartyRole, allocate_vehicle_party, get_customer_or_404
from app.sales.services.priced_deal import DealT, PricedDeal, lock_editable, project, require_editable
from app.valuation.public import list_valid_valuations_for_vehicle
from app.vehicle.public import create_or_get_vehicle_mdm, match_vehicle


@dataclasses.dataclass(frozen=True)
class _ResolvedVehicle:
    """The vehicle-mdm record a VIN resolved to — plain data, never the row."""

    id: uuid.UUID
    vin: str


def _lost_the_insert_race(exc: ConflictError, vin: str) -> bool:
    """vehicle-mdm's own refusal of a second row for this VIN — and nothing
    else, so any other conflict still reaches the caller."""

    details = exc.details or {}
    return "reason" not in details and details.get("vin") == vin


def _resolve_vehicle(db: Session, *, vin: str, actor_id: uuid.UUID) -> _ResolvedVehicle:
    """The vehicle-mdm record for `vin`, created if vehicle-mdm has never
    seen it — committed before this returns, on a session of its own
    (module docstring)."""

    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        try:
            vehicle, _created = create_or_get_vehicle_mdm(own, vin=vin, actor_id=actor_id)
        except ConflictError as exc:
            if not _lost_the_insert_race(exc, vin):
                raise
            # The guard rolled `own` back; the winner's row is committed.
            vehicle, _created = create_or_get_vehicle_mdm(own, vin=vin, actor_id=actor_id)
        return _ResolvedVehicle(id=vehicle.id, vin=vehicle.vin)


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

    # KAN-99: resolve the party customer in the caller's group BEFORE any
    # write — a customerId from another group is a 404 that must not leave
    # a committed vehicle_mdm row behind (_resolve_vehicle commits).
    party_customer_id = customer_id or deal.customer_id
    if party_customer_id is not None:
        get_customer_or_404(db, group_id, party_customer_id)

    resolved_vin = _resolve_trade_in_vin(db, vin=vin, plate=plate, canton=canton)
    vehicle = _resolve_vehicle(db, vin=resolved_vin, actor_id=actor_id)

    # FR-S-08: the customer's own vehicles are offered first by party role
    # — allocate as BOTH owner and keeper (one role per call, ADR-064).
    if party_customer_id is not None:
        allocate_vehicle_party(
            db, vehicle_id=vehicle.id, customer_id=party_customer_id, role=VehiclePartyRole.OWNER,
            group_id=group_id, actor_id=actor_id,
        )
        allocate_vehicle_party(
            db, vehicle_id=vehicle.id, customer_id=party_customer_id, role=VehiclePartyRole.KEEPER,
            group_id=group_id, actor_id=actor_id,
        )

    # KAN-309: the party allocations above commit the request session (each
    # foreign write is its own transaction, ADR-047; the vehicle resolution
    # commits a session of its own since KAN-328), which releases the row
    # lock the route took. A confirmation (or any other edit) may have
    # committed in between, so lock the deal again, re-read it, and check
    # it is still editable and still the version the caller sent before
    # writing it.
    lock_editable(db, deal, version_seen=version_seen)

    deal.trade_in_vehicle_id = vehicle.id
    deal.trade_in_vin = vehicle.vin
    deal.trade_in_label = vehicle_label

    # FR-S-08: "an existing valid valuation is offered before a new one is
    # made" — auto-attach the newest valid one if there is exactly the
    # unambiguous case; a caller with multiple candidates chooses via
    # attach_trade_in_valuation instead of relying on this default.
    existing = list_valid_valuations_for_vehicle(db, tenant_id=deal.tenant_id, vehicle_id=vehicle.id)
    if existing:
        _attach(deal, existing[0])

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
    _attach(deal, valuation)
    deal.updated_by = actor_id
    deal.version += 1
    _recompute_payable(deal)
    db.flush()
    project(db, deal)
    db.commit()
    db.refresh(deal)
    return deal


def _attach(deal: PricedDeal, valuation) -> None:
    deal.trade_in_valuation_id = valuation.id
    if valuation.configuration_id is not None:
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
