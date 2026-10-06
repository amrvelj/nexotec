"""Read-only report of vehicle-party closes one dealer group wrote into
another group's history before KAN-99 (KAN-139, ADR-014, ADR-064).

Before KAN-99, allocate_vehicle_party closed the open holder of
(vehicle, role) without a group filter: an allocation in group A could
close group B's holder for the same VIN, stamping the
`vehicle_party_remove` audit row and the `customer.vehicle_party.unlinked`
outbox event with group A. KAN-99 stopped new ones; this finds the rows
already written. The classification is
app.customer.reconciliation.find_cross_group_vehicle_party_closes.

READ-ONLY. It issues SELECTs only and rolls its session back; there is
no --commit. A repair (reopening a wrongly closed row, a compensating
event) is Anto's decision on the ticket, never this script's. The audit
log is append-only (app/core/audit.py): never edit or delete its rows.

Exit status: 0 when there is nothing cross-group or unresolved, 1
otherwise — so a run can gate a check without parsing the report.

Usage:
    DMS_DATABASE_URL=... python scripts/detect_cross_group_vehicle_party_closes.py
"""

import sys

from app.customer.reconciliation import CloseCategory, VehiclePartyCloseReport, find_cross_group_vehicle_party_closes
from app.db import SessionLocal


def render(report: VehiclePartyCloseReport) -> str:
    lines = ["KAN-139 cross-group vehicle-party close detection (read-only)", ""]
    counts = report.counts()
    for source in ("audit_event", "outbox_message"):
        per_source = counts.get(source, dict.fromkeys(CloseCategory, 0))
        total = sum(per_source.values())
        detail = ", ".join(f"{category.value}={per_source[category]}" for category in CloseCategory)
        lines.append(f"{source}: {total} close(s) — {detail}")
    for title, rows in (("CROSS-GROUP", report.findings), ("UNRESOLVED", report.unresolved)):
        lines += ["", f"{title}: {len(rows)}"]
        for row in rows:
            lines.append(
                f"  {row.source} {row.row_id} at {row.at.isoformat()} — customer {row.customer_id}"
                f" (group {row.customer_group_id}), stamped {row.stamped_tenant_id} (group {row.stamped_group_id})"
            )
    return "\n".join(lines)


def main() -> int:
    db = SessionLocal()
    try:
        report = find_cross_group_vehicle_party_closes(db)
    finally:
        db.rollback()
        db.close()
    print(render(report))
    return 1 if report.findings or report.unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
