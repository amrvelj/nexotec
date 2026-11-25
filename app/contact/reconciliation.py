"""Contact's outbound cross-context references (PR-2, repointed to
DealerGroup in WP-3 PR-2, ADR-014 — Contact and its child collections moved
from dealership-scoped to group-scoped). Everything here is read-only — see
app.core.reconciliation for the mechanism.
"""

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Iterable

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    Contact,
    ContactAddress,
    ContactEmail,
    ContactExternalId,
    ContactGroupAssignment,
    ContactLifecycleStatus,
    ContactNumberSequence,
    ContactPhone,
    ContactRole,
    ContactRoleAssignment,
    ContactTag,
)
from app.contact.models.legal_basis import LegalBasis
from app.contact.models.stock_item_booking import StockItemBooking
from app.contact.models.vehicle_party import VehicleParty
from app.contact.services.contact_common import CONTACT_AUDIT_ENTITY_TYPES
from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.integration.public import message_delivery_recipient_reference_check
from app.inventory.public import stock_item_reference_check
from app.platform.public import (
    actor_reference_checks,
    dealer_group_reference_check,
    dealership_reference_check,
    find_dealer_group_ids,
    find_dealership_group_ids,
    user_is_deactivated,
)
from app.vehicle.public import vehicle_is_merged, vehicle_reference_check

CONTEXT = "contact"

# How long a published event may wait for its consumer before a missing row
# is a finding: the outbox's normal delay, as Stock's and Sales' checks allow.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)
_BOOKING_EVENT_TYPES = ("inventory.stock_item.purchased", "inventory.stock_item.storno")
# KAN-464 PR-3 (ADR-210): every event this context publishes, and the name it
# was published under before customers became contacts. A row already in the
# outbox keeps its name (never rewritten). KAN-464 PR-5 retired the old-name
# subscriptions and payload keys; the history read below still reads the old
# rows, and the check below reports one still pending or dead, which no
# consumer will now handle.
_BEFORE_THE_RENAME = {
    "contact.created": "customer.created",
    "contact.updated": "customer.updated",
    "contact.merged": "customer.merged",
    "contact.vehicle_party.linked": "customer.vehicle_party.linked",
    "contact.vehicle_party.unlinked": "customer.vehicle_party.unlinked",
}
# KAN-464 PR-5: the facts other contexts publish whose contact keys the
# rename changed, and the keys they carried before it. Their consumers read
# only the new keys since PR-5; the check below reports an old one in flight.
_OLD_PAYLOAD_KEYS = {
    "inventory.stock_item.purchased": ("tradeInCustomerId",),
    "inventory.stock_item.storno": ("tradeInCustomerId", "returnedToCustomer"),
    "sales.contract.confirmed": ("customerId",),
}

# Every Contact table with created_by / updated_by: each must name a platform
# user, or be null (KAN-243; see app.platform.public.actor_reference_checks).
ACTOR_TABLES = (
    Contact, ContactPhone, ContactEmail, ContactAddress, ContactExternalId, ContactTag, LegalBasis, VehicleParty,
    ContactGroupAssignment,  # KAN-466
    ContactRoleAssignment,  # KAN-464
)

CHECKS: list[ReferenceCheck | StateCheck] = [
    # KAN-632 — the bookings this context learnt from Stock's purchase and
    # storno events (the trade-in consumers' record): each names a stock
    # item of a Betrieb that exists. Read across by design (ADR-107).
    stock_item_reference_check(
        label="contact_stock_item_booking.stock_item_id -> stock_item.id",
        source_model=StockItemBooking,
        source_row_id_column=StockItemBooking.event_id,
        source_fk_column=StockItemBooking.stock_item_id,
    ),
    dealership_reference_check(
        label="contact_stock_item_booking.tenant_id -> dealership.id",
        source_model=StockItemBooking,
        source_row_id_column=StockItemBooking.event_id,
        source_fk_column=StockItemBooking.tenant_id,
    ),
    StateCheck(
        # KAN-632 (rule 10): every purchase or storno event of a Betrieb has
        # its booking row — written by `contact.stock_item_bookings_on_*`,
        # seeded by the migration, re-seeded by
        # scripts/backfill_contact_stock_item_booking.py. One missing an
        # hour after it was published is an event the ordering guard of the
        # trade-in consumers cannot see (a worker without the booking
        # consumers during the deploy, a dead letter). Reads the core
        # outbox table, as reconciliation may.
        label="stock item booking event with no contact_stock_item_booking row",
        source_model=OutboxMessage,
        source_row_id_column=OutboxMessage.id,
        where=lambda: (
            OutboxMessage.event_type.in_(_BOOKING_EVENT_TYPES)
            & OutboxMessage.tenant_id.is_not(None)
            & (OutboxMessage.created_at < utcnow() - _OUTBOX_LAG_GRACE)
            & ~select(StockItemBooking.event_id)
            .where(StockItemBooking.event_id == OutboxMessage.id)
            .correlate(OutboxMessage)
            .exists()
        ),
    ),
    StateCheck(
        # KAN-464 (rule 10): an event published under its name before the
        # rename, still pending or dead an hour on. Since PR-5 nothing
        # subscribes to an old name, so a worker publishes such a row with no
        # consumer at its first poll (staging held no `customer.merged`,
        # 2026-11-25): this reports only a database no worker has polled yet,
        # such as one restored from before the rename. Never repaired here.
        label="event under its name before the rename to contacts, still pending or dead",
        source_model=OutboxMessage,
        source_row_id_column=OutboxMessage.id,
        where=lambda: (
            OutboxMessage.event_type.in_(tuple(_BEFORE_THE_RENAME.values()))
            & OutboxMessage.status.in_((OutboxStatus.PENDING, OutboxStatus.DEAD))
            & (OutboxMessage.created_at < utcnow() - _OUTBOX_LAG_GRACE)
        ),
    ),
    StateCheck(
        # KAN-464 PR-5 (rule 10): a Stock or Sales fact published before the
        # rename names the contact by its old key. Its consumers refuse such a
        # payload since PR-5, so a worker dead-letters it (with the alert);
        # this names those dead rows, and pending ones no worker has polled.
        label="stock or sales event naming the contact by its key before the rename, still pending or dead",
        source_model=OutboxMessage,
        source_row_id_column=OutboxMessage.id,
        where=lambda: (
            OutboxMessage.event_type.in_(tuple(_OLD_PAYLOAD_KEYS))
            & OutboxMessage.status.in_((OutboxStatus.PENDING, OutboxStatus.DEAD))
            & (OutboxMessage.created_at < utcnow() - _OUTBOX_LAG_GRACE)
            & or_(
                *(
                    (OutboxMessage.event_type == event_type) & cast(OutboxMessage.payload, String).contains(f'"{key}"')
                    for event_type, keys in _OLD_PAYLOAD_KEYS.items()
                    for key in keys
                )
            )
        ),
    ),
    vehicle_reference_check(
        # WP-5 PR-2 (ADR-021): a VehicleParty links a contact to the
        # physical vehicle, which in the three-layer model is VehicleMdm,
        # not the legacy `vehicle` table this used to target (retired by
        # KAN-79, ADR-153). Vehicle builds the check (KAN-123 step 6,
        # ADR-162): app.vehicle.public hands out no mapped class.
        label="vehicle_party.vehicle_id -> vehicle_mdm.id",
        source_model=VehicleParty,
        source_row_id_column=VehicleParty.id,
        source_fk_column=VehicleParty.vehicle_id,
    ),
    dealer_group_reference_check(
        label="contact.group_id -> dealer_group.id",
        source_model=Contact,
        source_row_id_column=Contact.id,
        source_fk_column=Contact.group_id,
    ),
    dealer_group_reference_check(
        label="contact_number_sequence.group_id -> dealer_group.id",
        source_model=ContactNumberSequence,
        source_row_id_column=ContactNumberSequence.group_id,
        source_fk_column=ContactNumberSequence.group_id,
    ),
    dealer_group_reference_check(
        label="contact_phone.group_id -> dealer_group.id",
        source_model=ContactPhone,
        source_row_id_column=ContactPhone.id,
        source_fk_column=ContactPhone.group_id,
    ),
    dealer_group_reference_check(
        label="contact_email.group_id -> dealer_group.id",
        source_model=ContactEmail,
        source_row_id_column=ContactEmail.id,
        source_fk_column=ContactEmail.group_id,
    ),
    dealer_group_reference_check(
        label="contact_external_id.group_id -> dealer_group.id",
        source_model=ContactExternalId,
        source_row_id_column=ContactExternalId.id,
        source_fk_column=ContactExternalId.group_id,
    ),
    # KAN-795 (rule 10, ADR-131): integration's delivery record names its
    # recipient. Integration sits below Contacts and cannot ask contact.public,
    # so the owner checks it here, through integration's builder (the call
    # points down; neither mapped class crosses a public module).
    message_delivery_recipient_reference_check(target_model=Contact, target_id_column=Contact.id),
    *actor_reference_checks(*ACTOR_TABLES),
    StateCheck(
        # KAN-634 (rule 10, ADR-131): a vehicle merge moves the duplicate's
        # party rows when contact consumes `vehicle.vehicle_mdm.merged`, in
        # a commit of its own after the merge's. A row still naming a merged
        # record is a delivery that has not happened (worker down, a dead
        # letter) — the fact arrived nowhere. A read of vehicle's table,
        # which only reconciliation may make (ADR-107). The copy the merge
        # kept on the duplicate on purpose (KAN-672, `merged_into_vehicle_id`)
        # is not a finding.
        label="vehicle_party.vehicle_id names a merged vehicle",
        source_model=VehicleParty,
        source_row_id_column=VehicleParty.id,
        where=lambda: (
            VehicleParty.merged_into_vehicle_id.is_(None)
            & vehicle_is_merged(VehicleParty.vehicle_id, correlate=VehicleParty)
        ),
    ),
    StateCheck(
        # KAN-200 (FR-A-37, rule 10): a contact whose advisor is deactivated —
        # the deactivation step hands every advised contact over; this says
        # when it did not (a hand-over that failed after the status change).
        label="contact.advisor_id names a deactivated user",
        source_model=Contact,
        source_row_id_column=Contact.id,
        where=lambda: (
            (Contact.lifecycle_status != ContactLifecycleStatus.MERGED)
            & user_is_deactivated(Contact.advisor_id, correlate=Contact)
        ),
    ),
    StateCheck(
        # KAN-464 (rule 10): a contact holds at least one role — every write
        # path keeps it so (create needs one, a PATCH cannot clear them, a
        # merge moves them); a contact with none is one a writer missed.
        label="contact holds no open contact_role",
        source_model=Contact,
        source_row_id_column=Contact.id,
        where=lambda: (
            (Contact.lifecycle_status != ContactLifecycleStatus.MERGED)
            & ~select(ContactRoleAssignment.id)
            .where(ContactRoleAssignment.contact_id == Contact.id, ContactRoleAssignment.valid_to.is_(None))
            .correlate(Contact)
            .exists()
        ),
    ),
    StateCheck(
        # KAN-464 (Q1, rule 10): a contact without the customer role carries
        # no customer field — no prospect/inactive status, no advisor, no
        # credit block. Every writer judges it under the contact row's lock;
        # this says when one did not.
        label="contact without the customer role carries a customer field",
        source_model=Contact,
        source_row_id_column=Contact.id,
        where=lambda: (
            # A merged tombstone gave its roles to the survivor and keeps its fields as history.
            (Contact.lifecycle_status != ContactLifecycleStatus.MERGED)
            & (Contact.lifecycle_status.in_([ContactLifecycleStatus.PROSPECT, ContactLifecycleStatus.INACTIVE])
             | Contact.advisor_id.is_not(None)
             | Contact.credit_block.is_(True))
            & ~select(ContactRoleAssignment.id)
            .where(
                ContactRoleAssignment.contact_id == Contact.id,
                ContactRoleAssignment.role == ContactRole.CUSTOMER,
                ContactRoleAssignment.valid_to.is_(None),
            )
            .correlate(Contact)
            .exists()
        ),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)


class StampCategory(str, enum.Enum):
    """How the scope stamped on a vehicle-party audit row or outbox event
    relates to the group of the contact it names (KAN-139, KAN-176)."""

    SAME_GROUP = "same_group"
    SAME_GROUP_DEALERSHIP_STAMP = "same_group_dealership_stamp"
    CROSS_GROUP = "cross_group"
    UNRESOLVED = "unresolved"


@dataclasses.dataclass(frozen=True)
class VehiclePartyStamp:
    source: str
    row_id: uuid.UUID
    at: dt.datetime
    contact_id: uuid.UUID | None
    contact_group_id: uuid.UUID | None
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
    `contact.vehicle_party.unlinked` outbox event with the CALLER's scope,
    while both name the closed holder's contact.

    Every such row is classified against the closed contact's group:
    - SAME_GROUP — stamped with the contact's own group.
    - SAME_GROUP_DEALERSHIP_STAMP — stamped with a dealership of that group.
      Before WP-3 PR-2 (026d3bb, 2026-08-25) contacts were dealership-
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
    - UNRESOLVED — the contact, or what the stamp names, no longer
      resolves, or the row carries no stamp. Reported, never guessed.

    Not a nightly ReconciliationCheck: the audit log is append-only, so a
    finding stays a finding after any repair and a recurring check would
    alarm forever. Detection only; it never writes, and it never joins
    across contexts in SQL — dealerships and groups are read through
    app.platform.public in their own queries, as the checks above do.
    """

    return _classify_vehicle_party_stamps(
        db, operation="close", audit_action="vehicle_party_remove", event_type="contact.vehicle_party.unlinked",
    )


def find_cross_group_vehicle_party_links(db: Session) -> VehiclePartyStampReport:
    """KAN-176 — the same read-only detection for the links the trade-in
    path wrote before KAN-99. Until then app.sales.services.trade_in passed
    the request body's contactId straight to allocate_vehicle_party, which
    did not resolve the contact in the caller's group: a trade-in in group
    A could link group B's contact to the vehicle, stamping the
    `vehicle_party_add` audit row and the `contact.vehicle_party.linked`
    outbox event with group A while both name group B's contact. KAN-99
    resolves the contact in the caller's group (404 otherwise); the rows
    already written remain, and KAN-139's detection covers closes only.

    Classified exactly as the closes are, against the linked contact's
    group. SAME_GROUP_DEALERSHIP_STAMP is a link written while contacts
    were dealership-scoped (before WP-3 PR-2, 026d3bb, 2026-08-25) — unlike
    a dealership-stamped close it has a VehicleParty row behind it. A
    CROSS_GROUP link's VehicleParty row still exists (closed or open) and
    its contact is in another group than the stamp: that row is the one a
    repair would have to judge. The contact-side POST stamps the
    contact's own group and can only ever be SAME_GROUP.

    Read-only and not a nightly check, for the reasons given on
    find_cross_group_vehicle_party_closes; a repair is Anto's decision.
    """

    return _classify_vehicle_party_stamps(
        db, operation="link", audit_action="vehicle_party_add", event_type="contact.vehicle_party.linked",
    )


def _classify_vehicle_party_stamps(
    db: Session, *, operation: str, audit_action: str, event_type: str
) -> VehiclePartyStampReport:
    """Classifies every contact audit row with `audit_action` and every
    outbox event of `event_type` by how its stamped tenant relates to the
    group of the contact it names — the one classification behind both
    detections above. SELECTs only; no cross-context join."""

    raw: list[tuple[str, uuid.UUID, dt.datetime, uuid.UUID | None, uuid.UUID | None]] = []
    for row_id, contact_id, stamped, at in db.execute(
        select(AuditEvent.id, AuditEvent.entity_id, AuditEvent.tenant_id, AuditEvent.created_at)
        .where(AuditEvent.entity_type.in_(CONTACT_AUDIT_ENTITY_TYPES), AuditEvent.action == audit_action)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all():
        raw.append(("audit_event", row_id, at, contact_id, stamped))
    for row_id, payload, stamped, at in db.execute(
        select(OutboxMessage.id, OutboxMessage.payload, OutboxMessage.tenant_id, OutboxMessage.occurred_at)
        .where(OutboxMessage.event_type.in_((event_type, _BEFORE_THE_RENAME[event_type])))
        .order_by(OutboxMessage.occurred_at, OutboxMessage.id)
    ).all():
        # KAN-464 PR-3: rows published before the rename name the customer
        contact_ref = payload.get("contactId", payload.get("customerId")) if isinstance(payload, dict) else None
        raw.append(("outbox_message", row_id, at, _as_uuid(contact_ref), stamped))

    contact_groups: dict[uuid.UUID, uuid.UUID] = {}
    for batch in _chunks({contact_id for *_, contact_id, _ in raw if contact_id is not None}):
        contact_groups.update(
            (contact_id, group_id)
            for contact_id, group_id in db.execute(select(Contact.id, Contact.group_id).where(Contact.id.in_(batch)))
        )

    stamps = {stamped for *_, stamped in raw if stamped is not None}
    known_groups: set[uuid.UUID] = set()
    dealership_groups: dict[uuid.UUID, uuid.UUID] = {}
    for batch in _chunks(stamps):
        known_groups.update(find_dealer_group_ids(db, batch))
        dealership_groups.update(find_dealership_group_ids(db, batch))

    rows: list[VehiclePartyStamp] = []
    for source, row_id, at, contact_id, stamped in raw:
        contact_group = contact_groups.get(contact_id) if contact_id is not None else None
        stamped_group: uuid.UUID | None = None
        if stamped in known_groups:
            stamped_group = stamped
        elif stamped is not None:
            stamped_group = dealership_groups.get(stamped)

        if contact_group is None or stamped_group is None:
            category = StampCategory.UNRESOLVED
        elif stamped_group != contact_group:
            category = StampCategory.CROSS_GROUP
        elif stamped == contact_group:
            category = StampCategory.SAME_GROUP
        else:
            category = StampCategory.SAME_GROUP_DEALERSHIP_STAMP
        rows.append(VehiclePartyStamp(
            source=source, row_id=row_id, at=at, contact_id=contact_id, contact_group_id=contact_group,
            stamped_tenant_id=stamped, stamped_group_id=stamped_group, category=category,
        ))
    return VehiclePartyStampReport(operation=operation, rows=rows)
