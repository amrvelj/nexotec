"""Customer's outbound cross-context references (PR-2, repointed to
DealerGroup in WP-3 PR-2, ADR-014 — Customer and its child collections moved
from dealership-scoped to group-scoped). Everything here is read-only — see
app.core.reconciliation for the mechanism.
"""

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.models.customer import (
    Customer,
    CustomerAddress,
    CustomerEmail,
    CustomerExternalId,
    CustomerNumberSequence,
    CustomerPhone,
    CustomerTag,
)
from app.customer.models.legal_basis import LegalBasis
from app.customer.models.vehicle_party import VehicleParty
from app.platform.public import DealerGroup, Dealership, actor_reference_checks
from app.vehicle.public import VehicleMdm

CONTEXT = "customer"

# Every Customer table with created_by / updated_by: each must name a platform
# user, or be null (KAN-243; see app.platform.public.actor_reference_checks).
ACTOR_TABLES = (
    Customer, CustomerPhone, CustomerEmail, CustomerAddress, CustomerExternalId, CustomerTag, LegalBasis, VehicleParty,
)

CHECKS = [
    ReferenceCheck(
        # WP-5 PR-2 (ADR-021): a VehicleParty links a customer to the
        # physical vehicle, which in the three-layer model is VehicleMdm,
        # not the legacy `vehicle` table this used to target. The legacy
        # table is being retired after the one-way migration
        # (scripts/migrate_legacy_vehicles.py); its seven-consecutive-
        # clean-nights exit criterion is only meaningful once this check
        # measures the surviving table. VehicleMdm comes through
        # app.vehicle.public — the only door.
        label="vehicle_party.vehicle_id -> vehicle_mdm.id",
        source_model=VehicleParty,
        source_row_id_column=VehicleParty.id,
        source_fk_column=VehicleParty.vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
    ),
    ReferenceCheck(
        label="customer.group_id -> dealer_group.id",
        source_model=Customer,
        source_row_id_column=Customer.id,
        source_fk_column=Customer.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_number_sequence.group_id -> dealer_group.id",
        source_model=CustomerNumberSequence,
        source_row_id_column=CustomerNumberSequence.group_id,
        source_fk_column=CustomerNumberSequence.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_phone.group_id -> dealer_group.id",
        source_model=CustomerPhone,
        source_row_id_column=CustomerPhone.id,
        source_fk_column=CustomerPhone.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_email.group_id -> dealer_group.id",
        source_model=CustomerEmail,
        source_row_id_column=CustomerEmail.id,
        source_fk_column=CustomerEmail.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_external_id.group_id -> dealer_group.id",
        source_model=CustomerExternalId,
        source_row_id_column=CustomerExternalId.id,
        source_fk_column=CustomerExternalId.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)


class StampCategory(str, enum.Enum):
    """How the scope stamped on a vehicle-party audit row or outbox event
    relates to the group of the customer it names (KAN-139, KAN-176)."""

    SAME_GROUP = "same_group"
    SAME_GROUP_DEALERSHIP_STAMP = "same_group_dealership_stamp"
    CROSS_GROUP = "cross_group"
    UNRESOLVED = "unresolved"


@dataclasses.dataclass(frozen=True)
class VehiclePartyStamp:
    source: str
    row_id: uuid.UUID
    at: dt.datetime
    customer_id: uuid.UUID | None
    customer_group_id: uuid.UUID | None
    stamped_tenant_id: uuid.UUID | None
    stamped_group_id: uuid.UUID | None
    category: StampCategory


@dataclasses.dataclass(frozen=True)
class VehiclePartyStampReport:
    operation: str  # "close" (KAN-139) or "link" (KAN-176)
    rows: list[VehiclePartyStamp]

    @property
    def findings(self) -> list[VehiclePartyStamp]:
        return [row for row in self.rows if row.category is StampCategory.CROSS_GROUP]

    @property
    def unresolved(self) -> list[VehiclePartyStamp]:
        return [row for row in self.rows if row.category is StampCategory.UNRESOLVED]

    def counts(self) -> dict[str, dict[StampCategory, int]]:
        counts: dict[str, dict[StampCategory, int]] = {}
        for row in self.rows:
            per_source = counts.setdefault(row.source, dict.fromkeys(StampCategory, 0))
            per_source[row.category] += 1
        return counts


_CHUNK = 1000


def _chunks(ids: Iterable[uuid.UUID]) -> Iterable[list[uuid.UUID]]:
    batch = list(ids)
    for start in range(0, len(batch), _CHUNK):
        yield batch[start:start + _CHUNK]


def _as_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def find_cross_group_vehicle_party_closes(db: Session) -> VehiclePartyStampReport:
    """KAN-139 — read-only detection of vehicle-party closes one dealer group
    wrote into another group's history before KAN-99. Until then
    allocate_vehicle_party closed the open holder of (vehicle, role) with no
    group filter and stamped the close's `vehicle_party_remove` audit row and
    `customer.vehicle_party.unlinked` outbox event with the CALLER's scope,
    while both name the closed holder's customer.

    Every such row is classified against the closed customer's group:
    - SAME_GROUP — stamped with the customer's own group.
    - SAME_GROUP_DEALERSHIP_STAMP — stamped with a dealership of that group.
      Before WP-3 PR-2 (026d3bb, 2026-08-25) customers were dealership-
      scoped and `vehicle_party_remove` was the D-12 Disconnect, which
      hard-deleted the row and stamped the dealership's tenant id. Such a
      row records a removal with no VehicleParty row behind it, and never
      has an outbox twin. A dealership is judged by its group today: one
      that moved groups since is classified by where it is now.
    - CROSS_GROUP — stamped with another group, or a dealership of another
      group. These are the findings. Close-then-open arrived with WP-5
      PR-9 (48100da, 2026-08-28), group-stamped from the start, so a
      genuine finding falls between then and KAN-99's deploy and its
      VehicleParty row still exists.
    - UNRESOLVED — the customer, or what the stamp names, no longer
      resolves, or the row carries no stamp. Reported, never guessed.

    Not a nightly ReconciliationCheck: the audit log is append-only, so a
    finding stays a finding after any repair and a recurring check would
    alarm forever. Detection only; it never writes, and it never joins
    across contexts in SQL — dealerships and groups are read through
    app.platform.public in their own queries, as the checks above do.
    """

    return _classify_vehicle_party_stamps(
        db, operation="close", audit_action="vehicle_party_remove", event_type="customer.vehicle_party.unlinked",
    )


def find_cross_group_vehicle_party_links(db: Session) -> VehiclePartyStampReport:
    """KAN-176 — the same read-only detection for the links the trade-in
    path wrote before KAN-99. Until then app.sales.services.trade_in passed
    the request body's customerId straight to allocate_vehicle_party, which
    did not resolve the customer in the caller's group: a trade-in in group
    A could link group B's customer to the vehicle, stamping the
    `vehicle_party_add` audit row and the `customer.vehicle_party.linked`
    outbox event with group A while both name group B's customer. KAN-99
    resolves the customer in the caller's group (404 otherwise); the rows
    already written remain, and KAN-139's detection covers closes only.

    Classified exactly as the closes are, against the linked customer's
    group. SAME_GROUP_DEALERSHIP_STAMP is a link written while customers
    were dealership-scoped (before WP-3 PR-2, 026d3bb, 2026-08-25) — unlike
    a dealership-stamped close it has a VehicleParty row behind it. A
    CROSS_GROUP link's VehicleParty row still exists (closed or open) and
    its customer is in another group than the stamp: that row is the one a
    repair would have to judge. The customer-side POST stamps the
    customer's own group and can only ever be SAME_GROUP.

    Read-only and not a nightly check, for the reasons given on
    find_cross_group_vehicle_party_closes; a repair is Anto's decision.
    """

    return _classify_vehicle_party_stamps(
        db, operation="link", audit_action="vehicle_party_add", event_type="customer.vehicle_party.linked",
    )


def _classify_vehicle_party_stamps(
    db: Session, *, operation: str, audit_action: str, event_type: str
) -> VehiclePartyStampReport:
    """Classifies every customer audit row with `audit_action` and every
    outbox event of `event_type` by how its stamped tenant relates to the
    group of the customer it names — the one classification behind both
    detections above. SELECTs only; no cross-context join."""

    raw: list[tuple[str, uuid.UUID, dt.datetime, uuid.UUID | None, uuid.UUID | None]] = []
    for row_id, customer_id, stamped, at in db.execute(
        select(AuditEvent.id, AuditEvent.entity_id, AuditEvent.tenant_id, AuditEvent.created_at)
        .where(AuditEvent.entity_type == "customer", AuditEvent.action == audit_action)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all():
        raw.append(("audit_event", row_id, at, customer_id, stamped))
    for row_id, payload, stamped, at in db.execute(
        select(OutboxMessage.id, OutboxMessage.payload, OutboxMessage.tenant_id, OutboxMessage.occurred_at)
        .where(OutboxMessage.event_type == event_type)
        .order_by(OutboxMessage.occurred_at, OutboxMessage.id)
    ).all():
        customer_ref = payload.get("customerId") if isinstance(payload, dict) else None
        raw.append(("outbox_message", row_id, at, _as_uuid(customer_ref), stamped))

    customer_groups: dict[uuid.UUID, uuid.UUID] = {}
    for batch in _chunks({customer_id for *_, customer_id, _ in raw if customer_id is not None}):
        customer_groups.update(
            (customer_id, group_id)
            for customer_id, group_id in db.execute(select(Customer.id, Customer.group_id).where(Customer.id.in_(batch)))
        )

    stamps = {stamped for *_, stamped in raw if stamped is not None}
    known_groups: set[uuid.UUID] = set()
    dealership_groups: dict[uuid.UUID, uuid.UUID] = {}
    for batch in _chunks(stamps):
        known_groups.update(db.scalars(select(DealerGroup.id).where(DealerGroup.id.in_(batch))).all())
        dealership_groups.update(
            (dealership_id, group_id)
            for dealership_id, group_id in db.execute(
                select(Dealership.id, Dealership.dealer_group_id).where(Dealership.id.in_(batch))
            )
        )

    rows: list[VehiclePartyStamp] = []
    for source, row_id, at, customer_id, stamped in raw:
        customer_group = customer_groups.get(customer_id) if customer_id is not None else None
        stamped_group: uuid.UUID | None = None
        if stamped in known_groups:
            stamped_group = stamped
        elif stamped is not None:
            stamped_group = dealership_groups.get(stamped)

        if customer_group is None or stamped_group is None:
            category = StampCategory.UNRESOLVED
        elif stamped_group != customer_group:
            category = StampCategory.CROSS_GROUP
        elif stamped == customer_group:
            category = StampCategory.SAME_GROUP
        else:
            category = StampCategory.SAME_GROUP_DEALERSHIP_STAMP
        rows.append(VehiclePartyStamp(
            source=source, row_id=row_id, at=at, customer_id=customer_id, customer_group_id=customer_group,
            stamped_tenant_id=stamped, stamped_group_id=stamped_group, category=category,
        ))
    return VehiclePartyStampReport(operation=operation, rows=rows)
