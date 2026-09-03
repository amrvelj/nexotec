"""One-way migration off the shipped `vehicle` table (WP-5 PR-7, ADR-021,
Risk A-13). Idempotent and re-runnable, keyed on
`app.vehicle.models.vehicle_mdm.VehicleMdm.migrated_from_legacy_vehicle_id`:
a row already migrated is reported and skipped, never migrated twice.

MANDATORY DRY RUN. Default mode is dry-run — it produces the row-level
reconciliation report below and writes NOTHING. Nothing commits until a
human has read that report and re-runs with --commit, per the brief's own
instruction ("nothing writes until I approve the report"). This script
cannot approve its own report.

Per-row outcome, in the report:
- `rejected`    — the legacy row has no valid 17-char ISO-3779 VIN. It
  cannot enter vehicle-mdm (VIN is mandatory, ADR-045 / PRD-Vehicles
  §Identifiers). No VIN is invented and the constraint is not relaxed.
- `matched`     — the VIN already resolves to a `vehicle_mdm` row (the
  FR-V-02 matching waterfall's `vin` rung, `app.vehicle.services.matching`).
  FR-V-04: a known VIN ATTACHES, never duplicates. The legacy id is
  stamped onto that existing row as provenance and its custody/odometer
  history is carried across; the row's own identity and status are left
  exactly as they are.
- `created`     — no `vehicle_mdm` row for this VIN. One is created,
  `unverified` (no catalogue_variant_id — real catalogue matching waits
  for WP-6's provider mirror), with a fresh `vehicle_number` (ADR-022).
- `ambiguous`   — the VIN matched a `vehicle_mdm` row that is itself
  merged away (`merged_into_vehicle_id`) to a survivor that is missing,
  or already carries a *different* legacy id. Reported, never guessed.
- `already_migrated` — this legacy id is already stamped on a `vehicle_mdm`
  row. A re-run reports every row here and changes nothing.
- `odometer_skipped_no_custodian` — an extra line: the legacy row has an
  odometer value but no `current_custodian_partner_id`, and
  `VehicleOdometerReading.recording_tenant_id` is NOT NULL. Flagged, not
  guessed.

Carried across on `matched` and `created` alike:
- The old table's own `vehicle_custody_event` rows, repointed at the
  surviving `vehicle_mdm` id — real, meaningful history.
- `old.odometer` -> one `VehicleOdometerReading` (source=IMPORT), only
  when a custodian is set (see above).

Deliberately NOT carried onto vehicle-mdm (no column for them, by design —
they belong to ModelVariant once a catalogue match exists, or to
inventory/aftersales): make, model, trim, engine, condition,
registration_status, registration_canton, energy_efficiency_category,
co2_emissions_gkm. On a `created` row `old.status` maps to the new
lifecycle-only VehicleStatus (LOSSY — ADR-040's whole point is that the
old enum conflated lifecycle with stock state); `totaled` -> `scrapped`
is an approximation, flagged per row.

Usage:
    DMS_DATABASE_URL=... python scripts/migrate_legacy_vehicles.py            # dry run (default)
    DMS_DATABASE_URL=... python scripts/migrate_legacy_vehicles.py --commit   # writes, after you've read the report
"""

import argparse
import dataclasses
import datetime as dt
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.validators import is_valid_vin
from app.db import SessionLocal
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCustodyEvent as LegacyCustodyEvent
from app.vehicle.models.vehicle import VehicleStatus as LegacyVehicleStatus
from app.vehicle.models.vehicle_history import CustodyEventType, OdometerSource
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleStatus
from app.vehicle.services.matching import match_vehicle
from app.vehicle.services.vehicle_history import record_custody_event, record_odometer_reading
from app.vehicle.services.vehicle_mdm import allocate_vehicle_number

_VIN_REJECTED_REASON = "no valid 17-char ISO-3779 VIN — cannot enter vehicle-mdm (VIN mandatory, ADR-045)"

_STATUS_MAP: dict[LegacyVehicleStatus, VehicleStatus] = {
    LegacyVehicleStatus.IN_TRANSIT: VehicleStatus.ACTIVE,
    LegacyVehicleStatus.IN_STOCK: VehicleStatus.ACTIVE,
    LegacyVehicleStatus.SOLD: VehicleStatus.ACTIVE,
    LegacyVehicleStatus.IN_SERVICE: VehicleStatus.ACTIVE,
    LegacyVehicleStatus.TOTALED: VehicleStatus.SCRAPPED,  # approximation — see module docstring
    LegacyVehicleStatus.SCRAPPED: VehicleStatus.SCRAPPED,
}

@dataclasses.dataclass
class RowOutcome:
    legacy_vehicle_id: uuid.UUID
    vin: str
    outcome: str
    new_vehicle_id: uuid.UUID | None = None
    notes: str = ""


@dataclasses.dataclass
class MigrationReport:
    committed: bool
    total_legacy_rows: int
    outcomes: list[RowOutcome]
    custody_events_carried: int
    aborted: bool = False

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.outcome] = counts.get(o.outcome, 0) + 1
        header = "COMMITTED" if self.committed else "DRY RUN — nothing written"
        if self.aborted:
            header = "ABORTED — a row errored; NOTHING was written, fix it and re-run"
        lines = [
            header,
            f"legacy rows examined: {self.total_legacy_rows}",
            *(f"  {outcome}: {count}" for outcome, count in sorted(counts.items())),
            f"custody events carried: {self.custody_events_carried}",
        ]
        needs_review = [
            o
            for o in self.outcomes
            if o.outcome in ("rejected", "ambiguous", "error") or "approximat" in o.notes.lower()
        ]
        if needs_review:
            lines.append(f"rows needing a human decision ({len(needs_review)}):")
            for o in needs_review:
                lines.append(f"  legacy {o.legacy_vehicle_id} (VIN {o.vin or '<none>'}) [{o.outcome}]: {o.notes}")
        return "\n".join(lines)


def _resolve_merge_chain(db: Session, vehicle: VehicleMdm) -> VehicleMdm | None:
    """Follow merged_into_vehicle_id to the surviving row. Returns None if
    the chain points at a row that no longer exists (a broken merge)."""

    seen: set[uuid.UUID] = set()
    current = vehicle
    while current.merged_into_vehicle_id is not None:
        if current.id in seen:  # defensive: a cycle should be impossible (merge is one-way)
            return None
        seen.add(current.id)
        nxt = db.get(VehicleMdm, current.merged_into_vehicle_id)
        if nxt is None:
            return None
        current = nxt
    return current


def _carry_history(db: Session, legacy: LegacyVehicle, target: VehicleMdm, *, commit: bool) -> int:
    """Odometer reading + the legacy row's own custody events, onto the
    surviving vehicle_mdm row. Returns the count of custody events seen
    (carried when commit=True)."""

    # A missing custodian is reported as `odometer_skipped_no_custodian`
    # by the caller — here we only carry the reading when there is an
    # honest recording_tenant_id to put on it (that column is NOT NULL).
    if legacy.odometer is not None and legacy.current_custodian_partner_id is not None and commit:
        record_odometer_reading(
            db,
            vehicle_id=target.id,
            value=legacy.odometer,
            reading_date=dt.datetime.now(dt.UTC).date(),
            source=OdometerSource.IMPORT,
            recording_tenant_id=legacy.current_custodian_partner_id,
        )

    legacy_events = list(
        db.scalars(select(LegacyCustodyEvent).where(LegacyCustodyEvent.vehicle_id == legacy.id)).all()
    )
    if commit:
        for old_event in legacy_events:
            record_custody_event(
                db,
                vehicle_id=target.id,
                partner_id=old_event.partner_id,
                event_type=CustodyEventType(old_event.event_type.value),
                event_date=old_event.event_date,
                transaction_id=old_event.transaction_id,
                actor_id=old_event.created_by,
            )
    return len(legacy_events)


def _migrate_row(db: Session, legacy: LegacyVehicle, *, commit: bool) -> tuple[list[RowOutcome], int]:
    """Classify and (when commit) migrate one legacy row. Returns its
    outcome line(s) and the number of custody events carried across."""

    if not is_valid_vin(legacy.vin):
        return [RowOutcome(legacy.id, legacy.vin or "", "rejected", notes=_VIN_REJECTED_REASON)], 0

    already = db.scalar(select(VehicleMdm).where(VehicleMdm.migrated_from_legacy_vehicle_id == legacy.id))
    if already is not None:
        return [RowOutcome(legacy.id, legacy.vin, "already_migrated", new_vehicle_id=already.id)], 0

    match = match_vehicle(db, vin=legacy.vin)
    if match.rung == "vin" and match.vehicle is not None:
        survivor = _resolve_merge_chain(db, match.vehicle)
        if survivor is None:
            return [
                RowOutcome(
                    legacy.id, legacy.vin, "ambiguous",
                    notes="VIN matched a vehicle_mdm row merged away to a survivor that no longer exists",
                )
            ], 0
        if (
            survivor.migrated_from_legacy_vehicle_id is not None
            and survivor.migrated_from_legacy_vehicle_id != legacy.id
        ):
            return [
                RowOutcome(
                    legacy.id, legacy.vin, "ambiguous", new_vehicle_id=survivor.id,
                    notes=f"VIN already attached to a different legacy id ({survivor.migrated_from_legacy_vehicle_id})",
                )
            ], 0
        target = survivor
        outcome = "matched" if commit else "would_attach"
        notes = ""
        if commit:
            target.migrated_from_legacy_vehicle_id = legacy.id
            db.flush()
    else:
        notes = "legacy status 'totaled' approximated as 'scrapped'" if legacy.status == LegacyVehicleStatus.TOTALED else ""
        target = VehicleMdm(
            vin=legacy.vin,
            vehicle_number=allocate_vehicle_number(db) if commit else "F-DRYRUN",
            catalogue_variant_id=None,
            catalogue_match_status=CatalogueMatchStatus.UNVERIFIED,
            vehicle_status=_STATUS_MAP[legacy.status],
            migrated_from_legacy_vehicle_id=legacy.id,
        )
        outcome = "created" if commit else "would_create"
        if commit:
            db.add(target)
            db.flush()

    lines = [RowOutcome(legacy.id, legacy.vin, outcome, new_vehicle_id=target.id if commit else None, notes=notes)]
    if legacy.odometer is not None and legacy.current_custodian_partner_id is None:
        lines.append(
            RowOutcome(
                legacy.id, legacy.vin, "odometer_skipped_no_custodian",
                notes=f"odometer={legacy.odometer} present but no recording tenant available",
            )
        )
    carried = _carry_history(db, legacy, target, commit=commit)
    return lines, carried


def run_migration(db: Session, *, commit: bool) -> MigrationReport:
    legacy_rows = list(db.scalars(select(LegacyVehicle)).all())
    outcomes: list[RowOutcome] = []
    custody_events_carried = 0

    for legacy in legacy_rows:
        try:
            row_outcomes, carried = _migrate_row(db, legacy, commit=commit)
        except Exception as exc:  # noqa: BLE001 — a migration tool reports, it does not crash
            db.rollback()
            outcomes.append(RowOutcome(legacy.id, legacy.vin or "", "error", notes=f"{type(exc).__name__}: {exc}"))
            return MigrationReport(
                committed=False,
                total_legacy_rows=len(legacy_rows),
                outcomes=outcomes,
                custody_events_carried=custody_events_carried,
                aborted=True,
            )
        outcomes.extend(row_outcomes)
        custody_events_carried += carried

    if commit:
        db.commit()
    else:
        db.rollback()

    return MigrationReport(
        committed=commit,
        total_legacy_rows=len(legacy_rows),
        outcomes=outcomes,
        custody_events_carried=custody_events_carried,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omit for a dry run (default) — read the report first.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = run_migration(db, commit=args.commit)
    finally:
        db.close()

    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
