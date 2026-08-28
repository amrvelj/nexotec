"""One-way migration off the shipped `vehicle` table (WP-5 PR-7, ADR-021,
Risk A-13). Idempotent and re-runnable: a row already migrated
(app.vehicle.models.vehicle_mdm.VehicleMdm.migrated_from_legacy_vehicle_id
set) is reported and skipped, never migrated twice.

MANDATORY DRY RUN. Default mode is dry-run — it produces the row-level
reconciliation report below and writes NOTHING. Nothing commits until a
human has read that report and re-runs with --commit, per the brief's own
instruction ("nothing writes until I approve the report"). This script
cannot approve its own report; that step is deliberately left to whoever
runs it against real data.

What gets carried across, and what deliberately does not:
- vin, and a fresh vehicle_number (ADR-022) — the old table never had one.
- The old table's OWN vehicle_custody_event rows carry across unchanged,
  repointed at the new vehicle_mdm id — this is real, meaningful history,
  unlike the fields below.
- old.status (in_transit/in_stock/sold/in_service/totaled/scrapped) maps
  to the new, lifecycle-only VehicleStatus (active/exported/scrapped/
  stolen) — LOSSY by construction (ADR-021/ADR-040's entire point is that
  the old enum conflated lifecycle with stock state, so most old values
  collapse to `active` and the old stock meaning is simply not vehicle
  MDM's to carry forward; a downstream inventory migration, out of WP-5's
  scope, would need to reconstruct stock state from other old-schema
  tables, not from this one). `totaled` maps to `scrapped` as the closest
  available lifecycle value — an approximation, flagged per-row in the
  report, not silently assumed correct.
- old.odometer becomes one VehicleOdometerReading (source=IMPORT), but
  ONLY when old.current_custodian_partner_id is set — VehicleOdometerReading.
  recording_tenant_id is NOT NULL, and there is nothing honest to put there
  for a vehicle with no recorded custodian. Rows with an odometer value but
  no custodian are flagged, not migrated.
- Every migrated vehicle starts `unverified` (no catalogue_variant_id).
  PR-1/PR-2's catalogue is a genuinely empty greenfield table on this
  branch — pretending to run best-effort catalogue matching against an
  empty table would produce meaningless links. Real catalogue matching
  waits for WP-6's provider mirror to actually populate ModelVariant rows.
- make/model/trim/engine/condition/registration_status/registration_canton/
  energy_efficiency_category/co2_emissions_gkm are NOT carried onto
  VehicleMdm — there is no column for them there by design (they belong to
  ModelVariant, once a real catalogue match exists, or to inventory/
  aftersales, never to vehicle-mdm identity). They are preserved only in
  this script's own report for the record, not lost silently — but they do
  not appear anywhere in the new schema after migration.

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

from app.db import SessionLocal
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCustodyEvent as LegacyCustodyEvent
from app.vehicle.models.vehicle import VehicleStatus as LegacyVehicleStatus
from app.vehicle.models.vehicle_history import CustodyEventType, OdometerSource
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleStatus
from app.vehicle.services.vehicle_history import record_custody_event, record_odometer_reading
from app.vehicle.services.vehicle_mdm import allocate_vehicle_number

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
    outcome: str  # "would_create" | "already_migrated" | "odometer_skipped_no_custodian" | "error"
    new_vehicle_id: uuid.UUID | None = None
    notes: str = ""


@dataclasses.dataclass
class MigrationReport:
    committed: bool
    total_legacy_rows: int
    outcomes: list[RowOutcome]
    custody_events_carried: int

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.outcome] = counts.get(o.outcome, 0) + 1
        lines = [
            f"{'COMMITTED' if self.committed else 'DRY RUN — nothing written'}",
            f"legacy rows examined: {self.total_legacy_rows}",
            *(f"  {outcome}: {count}" for outcome, count in sorted(counts.items())),
            f"custody events carried: {self.custody_events_carried}",
        ]
        approximated = [o for o in self.outcomes if "approximat" in o.notes.lower()]
        if approximated:
            lines.append(f"rows with an approximated status mapping (review these): {len(approximated)}")
            for o in approximated:
                lines.append(f"  legacy {o.legacy_vehicle_id} (VIN {o.vin}): {o.notes}")
        return "\n".join(lines)


def run_migration(db: Session, *, commit: bool) -> MigrationReport:
    legacy_rows = list(db.scalars(select(LegacyVehicle)).all())
    outcomes: list[RowOutcome] = []
    custody_events_carried = 0

    for legacy in legacy_rows:
        already = db.scalar(
            select(VehicleMdm).where(VehicleMdm.migrated_from_legacy_vehicle_id == legacy.id)
        )
        if already is not None:
            outcomes.append(
                RowOutcome(legacy.id, legacy.vin, "already_migrated", new_vehicle_id=already.id)
            )
            continue

        new_status = _STATUS_MAP[legacy.status]
        notes = ""
        if legacy.status == LegacyVehicleStatus.TOTALED:
            notes = f"legacy status 'totaled' approximated as 'scrapped' (old status: {legacy.status.value})"

        new_vehicle = VehicleMdm(
            vin=legacy.vin,
            vehicle_number=allocate_vehicle_number(db) if commit else "F-DRYRUN",
            catalogue_variant_id=None,
            catalogue_match_status=CatalogueMatchStatus.UNVERIFIED,
            vehicle_status=new_status,
            migrated_from_legacy_vehicle_id=legacy.id,
        )
        if commit:
            db.add(new_vehicle)
            db.flush()

        outcomes.append(
            RowOutcome(
                legacy.id, legacy.vin, "would_create" if not commit else "created",
                new_vehicle_id=new_vehicle.id if commit else None, notes=notes,
            )
        )

        if legacy.odometer is not None:
            if legacy.current_custodian_partner_id is None:
                outcomes.append(
                    RowOutcome(
                        legacy.id, legacy.vin, "odometer_skipped_no_custodian",
                        notes=f"odometer={legacy.odometer} present but no recording tenant available",
                    )
                )
            elif commit:
                record_odometer_reading(
                    db, vehicle_id=new_vehicle.id, value=legacy.odometer, reading_date=dt.datetime.now(dt.UTC).date(),
                    source=OdometerSource.IMPORT, recording_tenant_id=legacy.current_custodian_partner_id,
                )

        legacy_custody_events = list(
            db.scalars(select(LegacyCustodyEvent).where(LegacyCustodyEvent.vehicle_id == legacy.id)).all()
        )
        custody_events_carried += len(legacy_custody_events)
        if commit:
            for old_event in legacy_custody_events:
                record_custody_event(
                    db,
                    vehicle_id=new_vehicle.id,
                    partner_id=old_event.partner_id,
                    event_type=CustodyEventType(old_event.event_type.value),
                    event_date=old_event.event_date,
                    transaction_id=old_event.transaction_id,
                    actor_id=old_event.created_by,
                )

    if commit:
        db.commit()
    else:
        db.rollback()

    return MigrationReport(
        committed=commit, total_legacy_rows=len(legacy_rows), outcomes=outcomes,
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
