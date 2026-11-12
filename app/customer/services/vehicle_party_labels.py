"""The vehicle label on VehicleParty (KAN-84, rule 2; split from customer.py,
KAN-617): written when a row is opened, created or repointed, refreshed on
vehicle-mdm's summary_changed and nightly.
"""

import datetime as dt
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyVehicleLabel
from app.vehicle.public import VehicleSummary, get_vehicle_summaries

# --- KAN-84: the vehicle label on VehicleParty (CLAUDE.md rule 2). The
# vehicle's VIN/number/make/model/year/trim are copied onto the party row
# from app.vehicle.public.get_vehicle_summaries — never joined at read time.

_VEHICLE_LABEL_FIELDS = {
    "vehicle_vin": "vin",
    "vehicle_number": "vehicle_number",
    "vehicle_make": "make",
    "vehicle_model": "model",
    "vehicle_model_year": "model_year",
    "vehicle_trim": "trim",
}


def _label_changes(party: VehicleParty, summary: VehicleSummary) -> dict[str, object]:
    return {
        column: getattr(summary, field)
        for column, field in _VEHICLE_LABEL_FIELDS.items()
        if getattr(party, column) != getattr(summary, field)
    }


def _write_vehicle_labels(db: Session, parties: list[VehicleParty]) -> None:
    """Writes the current label onto each party row (dirty — committed by the
    caller's own transaction). A party whose vehicle no longer exists keeps
    whatever label it had: the nightly reconciliation reports the dangling id."""

    summaries = get_vehicle_summaries(db, [p.vehicle_id for p in parties])
    now = utcnow()
    for party in parties:
        summary = summaries.get(party.vehicle_id)
        if summary is None:
            continue
        for column, value in _label_changes(party, summary).items():
            setattr(party, column, value)
        party.vehicle_label_refreshed_at = now
        party.__dict__.pop("_vehicle_label_read_fill", None)


def _fill_unlabelled_vehicle_parties_for_read(db: Session, parties: list[VehicleParty]) -> None:
    """A row written before KAN-84 has no label until the nightly refresh
    reaches it. A read fills it in memory only: the label is held in a plain
    instance attribute that VehicleParty.vehicle prefers, outside ORM state,
    so nothing is flushed (a GET never writes) and a later write to the same
    object in the same session still sees the stored columns as empty and
    writes them."""

    unlabelled = [p for p in parties if p.vehicle_label_refreshed_at is None]
    if not unlabelled:
        return
    summaries = get_vehicle_summaries(db, [p.vehicle_id for p in unlabelled])
    for party in unlabelled:
        summary = summaries.get(party.vehicle_id)
        if summary is None:
            continue
        party.__dict__["_vehicle_label_read_fill"] = VehiclePartyVehicleLabel(
            id=party.vehicle_id,
            vin=summary.vin,
            vehicle_number=summary.vehicle_number,
            make=summary.make,
            model=summary.model,
            model_year=summary.model_year,
            trim=summary.trim,
        )


def oldest_vehicle_party_label_age_seconds(db: Session) -> float | None:
    """Age of the stalest stored vehicle label — what the worker heartbeat
    records as `dms.label.age_seconds{label="vehicle_party.vehicle"}` (CLAUDE.md
    rule 10). The nightly job restamps every row it can resolve, so a value
    well past a day means the job has stopped. None when no row has ever been
    labelled. Rows whose vehicle no longer exists are never restamped; they
    are the reconciliation's finding, not this alarm's, and are excluded."""

    oldest = db.scalar(
        select(func.min(VehicleParty.vehicle_label_refreshed_at)).where(
            VehicleParty.vehicle_label_refreshed_at.is_not(None)
        )
    )
    if oldest is None:
        return None
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.UTC)
    return (utcnow() - oldest).total_seconds()


_LABEL_REFRESH_BATCH = 500


def refresh_vehicle_party_labels(db: Session) -> int:
    """KAN-84 — the nightly job (registered in app.worker) that keeps every
    VehicleParty's vehicle label in step with the vehicle context, so a
    catalogue match or VIN correction made after the link shows within a
    day. Also the backfill for rows written before KAN-84. Walks the table
    in id order, one commit per batch; returns how many rows' labels changed.

    A row whose label changed is updated through the ORM (updated_at moves:
    what the API returns for it changed). A row whose label is already
    current only gets vehicle_label_refreshed_at stamped, with updated_at
    deliberately held — nothing a reader sees changed.
    """

    changed = 0
    last_id: uuid.UUID | None = None
    while True:
        # KAN-583: the batch's rows are locked FOR NO KEY UPDATE in id order
        # before any is written — the order every multi-row writer of a
        # vehicle's rows takes (the KAN-186 consumer, `_lock_role_rows`); the
        # bulk stamp below would otherwise lock in scan order.
        stmt = (
            select(VehicleParty)
            .order_by(VehicleParty.id)
            .limit(_LABEL_REFRESH_BATCH)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if last_id is not None:
            stmt = stmt.where(VehicleParty.id > last_id)
        batch = list(db.scalars(stmt).all())
        if not batch:
            return changed
        last_id = batch[-1].id

        changed += _relabel_vehicle_parties(db, batch)
        db.commit()


def _relabel_vehicle_parties(db: Session, parties: list[VehicleParty]) -> int:
    """Re-reads each party's vehicle label from `get_vehicle_summaries` and
    stamps `vehicle_label_refreshed_at` — flushed, never committed here.
    Returns how many rows' labels changed. A row whose label changed is
    updated through the ORM (updated_at moves); a row already current is
    only stamped, updated_at held; a row whose vehicle no longer exists is
    left alone (reconciliation reports it). The nightly job and the
    summary-changed consumer share it, so both apply the same rule."""

    summaries = get_vehicle_summaries(db, [p.vehicle_id for p in parties])
    now = utcnow()
    changed = 0
    unchanged_ids: list[uuid.UUID] = []
    for party in parties:
        summary = summaries.get(party.vehicle_id)
        if summary is None:
            continue
        party.__dict__.pop("_vehicle_label_read_fill", None)
        changes = _label_changes(party, summary)
        if changes:
            for column, value in changes.items():
                setattr(party, column, value)
            party.vehicle_label_refreshed_at = now
            changed += 1
        else:
            unchanged_ids.append(party.id)
    db.flush()
    if unchanged_ids:
        db.execute(
            update(VehicleParty)
            .where(VehicleParty.id.in_(unchanged_ids))
            .values(vehicle_label_refreshed_at=now, updated_at=VehicleParty.updated_at)
            .execution_options(synchronize_session=False)
        )
    return changed


def relabel_vehicle_parties_for_vehicle(db: Session, *, vehicle_id: uuid.UUID) -> int:
    """KAN-186 — what the consumer of `vehicle.vehicle_mdm.summary_changed`
    does: every party row naming the vehicle takes its current label, so a
    VIN correction or a new first registration shows on the Vehicles tab
    as soon as the worker delivers the event, not the next night. Flushed
    only; the consumer harness commits it with the processed_event row.

    Every dealer group's rows: the vehicle is one global fact (ADR-022), so
    each group's holder rows (KAN-99) name the same car and carry the same
    label — no group's customer is read, only the rows' own vehicle_id.

    The label is re-read from `app.vehicle.public.get_vehicle_summaries`,
    never taken from the event's payload: delivery is at-least-once and
    unordered, and an older event delivered after a newer one must not put
    its values back. The rows are locked FOR NO KEY UPDATE in id order —
    the order a customer merge locks its duplicate's rows in — and re-read
    under the lock."""

    rows = list(
        db.scalars(
            select(VehicleParty)
            .where(VehicleParty.vehicle_id == vehicle_id)
            .order_by(VehicleParty.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    if not rows:
        return 0
    return _relabel_vehicle_parties(db, rows)
