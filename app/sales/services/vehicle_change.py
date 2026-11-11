"""«Fahrzeug im Lager geändert» (KAN-553, ADR-115; Anto's ruling on KAN-33,
2026-11-03): an offer's or a contract's vehicle snapshot stays frozen
(ADR-041), and the seller is told when the stock car changed under it.

- `record_stock_commercial_change` — the consumer's write, on
  `inventory.stock_item.commercial_changed`, in Sales' own transaction
  (ADR-047): every draft or open offer and every pending contract on that
  stock item gets `vehicle_changed_at` and the changed field names (merged),
  one audit row each, and its deal row re-projected. A confirmed, invoiced
  or cancelled contract and a cancelled offer are never touched. A change
  older than the deal's own freeze is no change to it (delivery is
  unordered: the seller may already have refreshed past it). The version is
  not bumped — a flag is not an edit, and an open workspace's next autosave
  must not meet a 409 for it.
- `refresh_vehicle` — «Aktualisieren»: re-freezes the same car
  (`freeze_vehicle_snapshot(force=True)`), re-prices through the build-up,
  clears the flag, a new version, audited.
- `dismiss_vehicle_change` — «Ignorieren»: clears the flag only, a new
  version, audited.

Both actions need the deal editable (an offer draft, a pending contract,
`priced_deal.lock_editable`); a sent offer shows the banner without them.
"""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.priced_deal import DealT, PricedDeal, lock_editable
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot

# The field names Stock publishes, in the order the banner names them.
KNOWN_FIELDS = ("listPrice", "effectivePrice", "options", "exteriorColour", "odometerKm", "configurationLabel")

_OPEN_OFFER_STATUSES = (OfferStatus.DRAFT, OfferStatus.OPEN)


def _entity_type(deal: PricedDeal) -> str:
    return "sales_contract" if isinstance(deal, SalesContract) else "sales_offer"


def _merge(existing: Sequence[str] | None, changed: Sequence[str]) -> list[str]:
    fields = set(existing or ()) | {field for field in changed if field in KNOWN_FIELDS}
    return [field for field in KNOWN_FIELDS if field in fields]


def _project(db: Session, deal: PricedDeal) -> None:
    if isinstance(deal, SalesContract):
        upsert_deal_projection(db, contract=deal)
    else:
        upsert_deal_projection(db, offer=deal)


def record_stock_commercial_change(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    stock_item_id: uuid.UUID,
    changed_fields: Sequence[str],
    changed_at: dt.datetime,
) -> int:
    """Flags every open deal on the car. Flushes only (the consumer harness
    commits with the processed-event row). Returns how many were flagged."""

    offers = db.scalars(
        select(SalesOffer)
        .where(
            SalesOffer.tenant_id == tenant_id,
            SalesOffer.stock_item_id == stock_item_id,
            SalesOffer.status.in_(_OPEN_OFFER_STATUSES),
        )
        .with_for_update()
    ).all()
    contracts = db.scalars(
        select(SalesContract)
        .where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.stock_item_id == stock_item_id,
            SalesContract.status == ContractStatus.PENDING,
        )
        .with_for_update()
    ).all()
    flagged = 0
    deals: list[PricedDeal] = [*offers, *contracts]
    for deal in deals:
        if deal.vehicle_source != "stock" or deal.vehicle_snapshot_frozen_at is None:
            continue
        if changed_at <= deal.vehicle_snapshot_frozen_at:
            continue
        changed_before, fields_before = deal.vehicle_changed_at, deal.vehicle_changed_fields
        fields = _merge(deal.vehicle_changed_fields, changed_fields)
        if not fields:
            continue
        deal.vehicle_changed_fields = fields
        deal.vehicle_changed_at = max(changed_at, deal.vehicle_changed_at or changed_at)
        record_audit_event(
            db,
            entity_type=_entity_type(deal),
            entity_id=deal.id,
            tenant_id=deal.tenant_id,
            action="vehicle_changed_in_stock",
            actor_id=None,
            before={
                "vehicleChangedAt": changed_before.isoformat() if changed_before else None,
                "vehicleChangedFields": fields_before,
            },
            after={"vehicleChangedAt": deal.vehicle_changed_at.isoformat(), "vehicleChangedFields": fields},
            reason="inventory.stock_item.commercial_changed",
        )
        _project(db, deal)
        flagged += 1
    db.flush()
    return flagged


def _require_flag(deal: PricedDeal) -> None:
    if deal.vehicle_changed_at is None:
        raise ConflictError(
            "The stock vehicle has not changed since this deal was priced.",
            details={"reason": "vehicle_not_changed"},
        )


def refresh_vehicle(db: Session, *, deal: DealT, version_seen: int, actor_id: uuid.UUID | None) -> DealT:
    """«Aktualisieren»: the car's current data, re-frozen and re-priced."""

    lock_editable(db, deal, version_seen=version_seen)
    _require_flag(deal)
    before = {
        "grossPrice": str(deal.gross_price) if deal.gross_price is not None else None,
        "vehicleChangedFields": deal.vehicle_changed_fields,
    }
    freeze_vehicle_snapshot(db, deal=deal, force=True)
    db.flush()
    apply_build_up(db, deal=deal)
    deal.vehicle_changed_at = None
    deal.vehicle_changed_fields = None
    deal.updated_by = actor_id
    deal.version += 1
    db.flush()
    record_audit_event(
        db,
        entity_type=_entity_type(deal),
        entity_id=deal.id,
        tenant_id=deal.tenant_id,
        action="vehicle_refreshed",
        actor_id=actor_id,
        before=before,
        after={"grossPrice": str(deal.gross_price) if deal.gross_price is not None else None},
    )
    _project(db, deal)
    db.commit()
    db.refresh(deal)
    return deal


def dismiss_vehicle_change(db: Session, *, deal: DealT, version_seen: int, actor_id: uuid.UUID | None) -> DealT:
    """«Ignorieren»: the deal keeps its frozen car; the flag goes."""

    lock_editable(db, deal, version_seen=version_seen)
    _require_flag(deal)
    before = {"vehicleChangedFields": deal.vehicle_changed_fields}
    deal.vehicle_changed_at = None
    deal.vehicle_changed_fields = None
    deal.updated_by = actor_id
    deal.version += 1
    db.flush()
    record_audit_event(
        db,
        entity_type=_entity_type(deal),
        entity_id=deal.id,
        tenant_id=deal.tenant_id,
        action="vehicle_change_dismissed",
        actor_id=actor_id,
        before=before,
        after={"vehicleChangedFields": None},
    )
    _project(db, deal)
    db.commit()
    db.refresh(deal)
    return deal
