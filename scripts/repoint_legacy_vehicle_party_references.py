"""Defensive, idempotent pass repointing any `VehicleParty` row still
pointing at a legacy `vehicle` id onto its `vehicle_mdm` replacement
(KAN-31, ADR-064, ADR-021).

Why this exists rather than being folded into scripts/migrate_legacy_
vehicles.py: that migration moves *vehicle* rows and never touched
VehicleParty; app.vehicle.reconciliation::
count_unrepointed_legacy_vehicle_party_references (WP-5 PR-7) COUNTS
stray references as a retirement-readiness gate but never repoints them.
Before KAN-31, `create_customer_vehicle` resolved against the legacy
table and inserted a raw VehicleParty — so any row it ever created
already pointed at a legacy id, and this is the pass that would have
repointed it once vehicle_mdm resolution landed.

As of KAN-31 there are ZERO VehicleParty rows in any known environment
(the customer-side create path 404'd on every real attempt before this
fix — Gap Analysis G-35). This script is a safety net, not a live
migration: run it once after deploying KAN-31, expect an empty report,
and keep it for any environment this session did not check.

MANDATORY DRY RUN. Default mode reports and writes NOTHING; --commit
writes after a human has read the report.

Per-row outcome:
- `already_mdm`      — vehicle_id already resolves to a vehicle_mdm row.
                        No-op, reported for completeness.
- `repointed`        — vehicle_id was a legacy vehicle.id with a known
                        vehicle_mdm.migrated_from_legacy_vehicle_id
                        replacement. Updated in place (same row, new
                        vehicle_id) — never a new row, never a delete.
- `unresolved`       — vehicle_id matches neither a vehicle_mdm row nor a
                        known legacy id with a migrated replacement.
                        REPORTED, never guessed at, never dropped.

Usage:
    DMS_DATABASE_URL=... python scripts/repoint_legacy_vehicle_party_references.py            # dry run (default)
    DMS_DATABASE_URL=... python scripts/repoint_legacy_vehicle_party_references.py --commit    # writes, after reading the report
"""

import argparse
import dataclasses
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models.vehicle_party import VehicleParty
from app.db import SessionLocal
from app.vehicle.models.vehicle_mdm import VehicleMdm


@dataclasses.dataclass
class RowOutcome:
    vehicle_party_id: uuid.UUID
    old_vehicle_id: uuid.UUID
    outcome: str
    new_vehicle_id: uuid.UUID | None = None


@dataclasses.dataclass
class RepointReport:
    committed: bool
    total_rows: int
    outcomes: list[RowOutcome]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for row in self.outcomes:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        lines = [
            f"repoint_legacy_vehicle_party_references: {'COMMITTED' if self.committed else 'DRY RUN (nothing written)'}",
            f"  total VehicleParty rows examined: {self.total_rows}",
        ]
        for outcome, count in sorted(counts.items()):
            lines.append(f"  {outcome}: {count}")
        for row in self.outcomes:
            if row.outcome == "unresolved":
                lines.append(f"    UNRESOLVED vehicle_party={row.vehicle_party_id} vehicle_id={row.old_vehicle_id}")
            elif row.outcome == "repointed":
                lines.append(
                    f"    repointed vehicle_party={row.vehicle_party_id} {row.old_vehicle_id} -> {row.new_vehicle_id}"
                )
        return "\n".join(lines)


def run_repoint(db: Session, *, commit: bool) -> RepointReport:
    parties = list(db.scalars(select(VehicleParty)).all())
    mdm_ids = set(db.scalars(select(VehicleMdm.id)).all())
    legacy_to_mdm = {
        legacy_id: mdm_id
        for mdm_id, legacy_id in db.execute(
            select(VehicleMdm.id, VehicleMdm.migrated_from_legacy_vehicle_id).where(
                VehicleMdm.migrated_from_legacy_vehicle_id.is_not(None)
            )
        ).all()
    }

    outcomes: list[RowOutcome] = []
    for party in parties:
        if party.vehicle_id in mdm_ids:
            outcomes.append(RowOutcome(party.id, party.vehicle_id, "already_mdm"))
            continue
        replacement = legacy_to_mdm.get(party.vehicle_id)
        if replacement is None:
            outcomes.append(RowOutcome(party.id, party.vehicle_id, "unresolved"))
            continue
        outcomes.append(RowOutcome(party.id, party.vehicle_id, "repointed", new_vehicle_id=replacement))
        if commit:
            party.vehicle_id = replacement

    if commit:
        db.commit()
    else:
        db.rollback()

    return RepointReport(committed=commit, total_rows=len(parties), outcomes=outcomes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omit for a dry run (default) — read the report first.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = run_repoint(db, commit=args.commit)
    finally:
        db.close()

    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
