"""Read-only report of vehicle-party closes (KAN-139) and links (KAN-176)
one dealer group wrote into another group's history before KAN-99
(ADR-014, ADR-064).

Before KAN-99, allocate_vehicle_party closed the open holder of
(vehicle, role) without a group filter: an allocation in group A could
close group B's holder for the same VIN, stamping the
`vehicle_party_remove` audit row and the `customer.vehicle_party.unlinked`
outbox event with group A. The trade-in path also passed a request-body
customerId through without resolving it in the caller's group, so group
A could link group B's customer, stamping the `vehicle_party_add` audit
row and the `customer.vehicle_party.linked` event with group A. KAN-99
stopped new ones; this finds the rows already written. The
classification is app.customer.reconciliation's
find_cross_group_vehicle_party_closes and
find_cross_group_vehicle_party_links; both run here, in one transaction.

READ-ONLY, enforced by the database: on Postgres the session runs in a
`SET TRANSACTION READ ONLY` transaction, so any write would fail rather
than land; it is rolled back at the end. There is no --commit. A repair
(reopening a wrongly closed row, closing a wrongly linked one, a
compensating event) is Anto's decision on the ticket, never this
script's. The audit log is append-only (app/core/audit.py): never edit
or delete its rows.

Exit status: 0 when neither report has anything cross-group or
unresolved, 1 otherwise — so a run can gate a check without parsing the
report.

Usage:
    DMS_DATABASE_URL=... python scripts/detect_cross_group_vehicle_party_closes.py
"""

import sys

from sqlalchemy import text

from app.customer.reconciliation import (
    StampCategory,
    VehiclePartyStampReport,
    find_cross_group_vehicle_party_closes,
    find_cross_group_vehicle_party_links,
)
from app.db import SessionLocal

_TITLES = {
    "close": "KAN-139 cross-group vehicle-party close detection (read-only)",
    "link": "KAN-176 cross-group vehicle-party link detection (read-only)",
}


def render(report: VehiclePartyStampReport) -> str:
    noun = report.operation
    lines = [_TITLES[noun], ""]
    counts = report.counts()
    for source in ("audit_event", "outbox_message"):
        per_source = counts.get(source, dict.fromkeys(StampCategory, 0))
        total = sum(per_source.values())
        detail = ", ".join(f"{category.value}={per_source[category]}" for category in StampCategory)
        lines.append(f"{source}: {total} {noun}(s) — {detail}")
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
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        reports = [find_cross_group_vehicle_party_closes(db), find_cross_group_vehicle_party_links(db)]
    finally:
        db.rollback()
        db.close()
    print("\n\n".join(render(report) for report in reports))
    return 1 if any(report.findings or report.unresolved for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
