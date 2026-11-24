"""Contact merge (FR-08; split from contact.py, KAN-617): the locks taken in
order (advisory, contact rows by id, party rows), and the re-point of the
duplicate's party rows, earlier merges, contacts, external IDs and tags onto
the survivor (KAN-177, KAN-356, KAN-372, KAN-383).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contact.models.contact import (
    Contact,
    ContactAddress,
    ContactEmail,
    ContactExternalId,
    ContactLifecycleStatus,
    ContactPhone,
    ContactTag,
)
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.contact.services import contact_groups as contact_groups_service
from app.contact.services import roles as roles_service
from app.contact.services.contact_common import (
    _CONTACT_TYPE_COLUMN,
    _EVENT_PRODUCER,
    _contact_label_payload,
    _fixup_single_primary,
    _group_scoped_or_404,
)
from app.contact.services.vehicle_party import _close_vehicle_party
from app.contact.services.vehicle_party_locks import _lock_vehicle_role_in_group, _try_lock_vehicle_role_in_group
from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import BadRequestError, ConflictError
from app.core.outbox import OutboxEvent
from app.core.outbox import publish as publish_event

# KAN-392: the audit reason on a kept vehicle-link copy a later merge re-points.
CONTACT_MERGED_REASON = "contact_merged"

def _lock_merge_contacts(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """KAN-372: a merge locks both contact rows — the duplicate FOR NO KEY
    UPDATE (KAN-365), the survivor FOR SHARE — and decides on their committed
    status under those locks. The survivor's status was read without a lock,
    and re-pointing rows onto it takes only the foreign key's KEY SHARE, which
    does not wait for the survivor's own merge: so a merge X→D could commit
    while D→T was in flight and leave X's rows, and X's
    `duplicateOfContactId`, on a merged contact. FOR SHARE waits for that
    merge's FOR NO KEY UPDATE; the re-read then finds D merged (409
    `merge_target_merged`). The duplicate is re-read too, so a second merge of
    the same contact that waited for the first is refused
    (`contact_already_merged`) instead of overwriting its survivor.

    Lock order: every merge takes its advisory locks first (KAN-356, sorted),
    then these two rows in id order. Two merges in opposite directions (A→B,
    B→A) therefore ask for the two rows in the same order and the second
    waits; without the order each would hold its duplicate and wait for the
    other's. Allocation and a reopen take one contact row FOR SHARE after
    their advisory lock, compatible with a merge's FOR SHARE on its survivor
    and waiting, as KAN-365 intends, for its FOR NO KEY UPDATE on the
    duplicate."""

    for contact_id in sorted((duplicate_id, target_id)):
        stmt = select(Contact.id).where(Contact.id == contact_id)
        if contact_id == duplicate_id:
            db.execute(stmt.with_for_update(key_share=True))
        else:
            db.execute(stmt.with_for_update(read=True))
    status: dict[uuid.UUID, ContactLifecycleStatus] = {
        row.id: row.lifecycle_status
        for row in db.execute(
            select(Contact.id, Contact.lifecycle_status).where(Contact.id.in_((duplicate_id, target_id)))
        )
    }
    if status[target_id] == ContactLifecycleStatus.MERGED:
        raise ConflictError(
            "Cannot merge into a contact that has itself been merged.",
            details={"reason": "merge_target_merged", "duplicateOfContactId": str(target_id)},
        )
    if status[duplicate_id] == ContactLifecycleStatus.MERGED:
        raise ConflictError(
            "Contact has already been merged.",
            details={"reason": "contact_already_merged", "currentLifecycleStatus": "merged"},
        )


def _repoint_vehicle_parties(
    db: Session,
    *,
    duplicate_id: uuid.UUID,
    target_id: uuid.UUID,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    group_id: uuid.UUID,
) -> tuple[int, list[uuid.UUID]]:
    """Re-points the duplicate's party rows at the survivor; returns how
    many, and the ids of the rows it KEPT instead.

    KAN-177 (ADR-064: party history is never deleted; Anto's ruling,
    2026-10-12, option A). A row whose (vehicle, role, effective_from) the
    survivor already holds cannot be re-pointed — uq_vehicle_party_scope is
    (vehicle, contact, role, effective_from), so the survivor's own row
    would collide — and before KAN-177 it was hard-deleted, unaudited. Now
    it stays on the duplicate's contact_id (the one place it does not
    collide), closed at its own start, and marked with the survivor it was
    merged into (`merged_into_contact_id`). The close goes through
    _close_vehicle_party, so it is audited (`vehicle_party_remove`, with
    `mergedIntoContactId`) and published (`contact.vehicle_party.unlinked`)
    like every other close; the merge's own audit event lists the ids.

    A copy that was already closed in the past keeps its own effective_to —
    that is recorded history, its unlink already a published fact — and only
    takes the mark, with no second close, audit row or event: the merge's own
    audit event lists its id. One that is open, or closed at a future date,
    is closed at its start so it never reads as a current party again. The
    survivor's row is not touched.

    KAN-356: every allocation and reopen serialises on the (vehicle, role,
    group) advisory lock, and the merge writes the same rows, so it takes that
    lock for every (vehicle, role) the duplicate has a row in — in sorted
    order, so two merges never deadlock each other — before it reads a row,
    then locks the rows it writes (advisory, then row: KAN-148's order). A
    row an allocation added for the duplicate while the merge waited can name
    a pair not locked yet, so the pairs are read again until every one is
    held. Such a late pair is out of the sorted order, so the merge never
    waits for it: it tries the lock, and if another writer holds it the merge
    is refused (409 `merge_vehicle_party_busy`, nothing written) and can
    simply be sent again.
    """

    held: set[tuple[uuid.UUID, VehiclePartyRole]] = set()
    duplicate_locked = False
    while True:
        pairs = {
            (vehicle_id, role)
            for vehicle_id, role in db.execute(
                select(VehicleParty.vehicle_id, VehicleParty.role)
                .where(VehicleParty.contact_id == duplicate_id)
                .distinct()
            ).all()
        }
        missing = sorted(pairs - held, key=lambda pair: (str(pair[0]), pair[1].value))
        if not missing:
            if duplicate_locked:
                break
            # KAN-365: an allocation to the duplicate reads its contact row
            # FOR SHARE under its own advisory lock before it inserts. Once
            # this lock is granted, every such allocation has either committed
            # (its pair is read again below) or waits for the merge and then
            # finds the contact merged.
            _lock_merge_contacts(db, duplicate_id=duplicate_id, target_id=target_id)
            duplicate_locked = True
            continue
        late = bool(held) or duplicate_locked
        for vehicle_id, role in missing:
            if not late:
                _lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id)
            elif not _try_lock_vehicle_role_in_group(db, vehicle_id=vehicle_id, role=role, group_id=group_id):
                raise ConflictError(
                    "A vehicle of this contact is being changed right now; send the merge again.",
                    details={"reason": "merge_vehicle_party_busy", "vehicleId": str(vehicle_id), "role": role.value},
                )
            held.add((vehicle_id, role))

    target_keys = {
        (p.vehicle_id, p.role, p.effective_from)
        for p in db.scalars(select(VehicleParty).where(VehicleParty.contact_id == target_id)).all()
    }
    repointed = 0
    kept_closed: list[uuid.UUID] = []
    duplicate_rows = (
        select(VehicleParty)
        .where(VehicleParty.contact_id == duplicate_id)
        .order_by(VehicleParty.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for party in db.scalars(duplicate_rows).all():
        key = (party.vehicle_id, party.role, party.effective_from)
        if key in target_keys:
            already_closed = party.effective_to is not None and party.effective_to <= utcnow()
            if already_closed:
                party.merged_into_contact_id = target_id
            else:
                _close_vehicle_party(
                    db,
                    party=party,
                    actor_id=actor_id,
                    acting_dealership_id=acting_dealership_id,
                    group_id=group_id,
                    effective_to=party.effective_from,
                    merged_into_contact_id=target_id,
                )
            kept_closed.append(party.id)
        else:
            party.contact_id = target_id
            target_keys.add(key)
            repointed += 1
    return repointed, kept_closed


def _repoint_earlier_merges(
    db: Session,
    *,
    duplicate_id: uuid.UUID,
    target_id: uuid.UUID,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    group_id: uuid.UUID,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Returns the re-pointed contacts' ids and the re-pointed copies' ids.

    KAN-383 (Anto, 2026-10-18, option A): a contact X merged into this
    duplicate D earlier names D in `duplicate_of_contact_id`; once D is
    merged into T, X would name a merged contact. Every such X is re-pointed
    to T in the merge's own transaction, its version bumped and the change
    audited on X (`merge_survivor_repointed`), so "merged into" always names a
    live contact. No event is published: X's rows already moved to D with its
    own merge and move on to T with this one (the `contact.merged` event for
    D carries them).

    The vehicle-party copies an earlier merge kept on X (KAN-177) name D in
    `merged_into_contact_id`; they are re-pointed to T the same way, so a
    `merged_party_row` refusal never names a merged contact. Each moved
    copy is audited on X as a `vehicle_party_update` (before/after
    `mergedIntoContactId`, reason `contact_merged`), and the caller lists
    the copies in the merge's own audit (`vehiclePartyCopiesRepointedIds`) —
    KAN-392, Anto 2026-10-31, option B: the History explains why a copy now
    names T. Chains written before KAN-383 are not repaired.

    Read after the merge holds D's row FOR NO KEY UPDATE (KAN-372): a merge
    X→D still in flight holds D FOR SHARE, so this merge waits for it to
    commit and then finds X here — the race order KAN-372 could not refuse
    ends with X naming T as well. The X rows are locked in id order, after
    the merge's own party rows: the one place a merge locks a contact row
    outside "advisory → contact rows → party rows". No writer holding an X
    row waits on this merge: allocation, a reopen and a merge into or of X
    refuse a merged contact (a reopen of a kept copy locks that copy first,
    but after its advisory lock, and waits for nothing after it); a
    credit-block change on X takes only X's row; the nightly advisor-label
    refresh skips a contact it cannot lock and waits for nothing (KAN-403).
    The copies are only tried, never waited for: the nightly vehicle-party
    label refresh and the summary-changed consumer (KAN-186) lock party rows
    in id order and could hold a copy while they wait for one of D's rows, so a copy held
    by another writer refuses the merge (409 `merge_vehicle_party_busy`,
    nothing written), which can simply be sent again."""

    earlier = list(
        db.scalars(
            select(Contact)
            .where(Contact.duplicate_of_contact_id == duplicate_id, Contact.group_id == group_id)
            .order_by(Contact.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).all()
    )
    copies_naming_d = (
        select(VehicleParty)
        .join(Contact, Contact.id == VehicleParty.contact_id)
        .where(VehicleParty.merged_into_contact_id == duplicate_id, Contact.group_id == group_id)
        .order_by(VehicleParty.id)
    )
    # A merge into D is what writes such a copy, and it needs D's row, which
    # this merge holds: the set cannot grow between these two reads.
    copy_count = len(db.scalars(copies_naming_d.with_only_columns(VehicleParty.id)).all())
    kept_copies = list(db.scalars(copies_naming_d.with_for_update(of=VehicleParty, skip_locked=True)).all())
    if len(kept_copies) < copy_count:
        raise ConflictError(
            "A vehicle of this contact is being changed right now; send the merge again.",
            details={"reason": "merge_vehicle_party_busy"},
        )
    for copy in kept_copies:
        copy.merged_into_contact_id = target_id
        record_audit_event(
            db,
            entity_type="contact",
            entity_id=copy.contact_id,
            tenant_id=group_id,
            action="vehicle_party_update",
            actor_id=actor_id,
            acting_dealership_id=acting_dealership_id,
            before={
                "vehicleId": str(copy.vehicle_id),
                "role": copy.role.value,
                "mergedIntoContactId": str(duplicate_id),
            },
            after={"mergedIntoContactId": str(target_id)},
            reason=CONTACT_MERGED_REASON,
        )
    for merged in earlier:
        merged.duplicate_of_contact_id = target_id
        merged.updated_by = actor_id
        merged.version += 1
        record_audit_event(
            db,
            entity_type="contact",
            entity_id=merged.id,
            tenant_id=group_id,
            action="merge_survivor_repointed",
            actor_id=actor_id,
            acting_dealership_id=acting_dealership_id,
            before={"duplicateOfContactId": str(duplicate_id)},
            after={"duplicateOfContactId": str(target_id)},
        )
    return [merged.id for merged in earlier], [copy.id for copy in kept_copies]


def _repoint_contacts(
    db: Session, model: type, unique_fields: tuple[str, ...], *, duplicate_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[int, int]:
    """Shared logic for ContactPhone/ContactEmail/ContactAddress:
    re-point onto the survivor unless it already has a row with the same
    identifying fields, in which case the duplicate's row is dropped as an
    exact duplicate. unique_fields is a tuple (not always length 1) because
    ContactAddress has no single natural key the way phone_e164/
    email_address are — its dedup key is composite.

    Afterwards, is_primary is re-collapsed per (contact, type) — not once
    per contact — since a merge can leave more than one primary within a
    type-group when both sides already had one of that type (ADR-067).
    """

    def _key(row: Any) -> tuple:
        return tuple(getattr(row, field) for field in unique_fields)

    target_rows: list[Any] = list(
        db.scalars(select(model).where(model.contact_id == target_id)).all()  # type: ignore[attr-defined]
    )
    target_keys = {_key(row) for row in target_rows}
    repointed = dropped = 0
    duplicate_rows: list[Any] = list(
        db.scalars(select(model).where(model.contact_id == duplicate_id)).all()  # type: ignore[attr-defined]
    )
    for row in duplicate_rows:
        key = _key(row)
        if key in target_keys:
            db.delete(row)
            dropped += 1
        else:
            row.contact_id = target_id
            target_keys.add(key)
            repointed += 1
    if repointed:
        db.flush()
        type_column = _CONTACT_TYPE_COLUMN[model]
        target_rows_after: list[Any] = list(
            db.scalars(select(model).where(model.contact_id == target_id)).all()  # type: ignore[attr-defined]
        )
        types_present = {getattr(row, type_column) for row in target_rows_after}
        for type_value in types_present:
            _fixup_single_primary(db, model, contact_id=target_id, type_value=type_value)
    return repointed, dropped


def _repoint_external_ids(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    """system_name is unique per contact (uq_contact_external_id_contact_
    system) — if the survivor already has a link for that system, its own
    linkage wins and the duplicate's is dropped rather than overwritten.
    """

    target_systems = {
        row.system_name
        for row in db.scalars(select(ContactExternalId).where(ContactExternalId.contact_id == target_id)).all()
    }
    repointed = dropped = 0
    for row in db.scalars(select(ContactExternalId).where(ContactExternalId.contact_id == duplicate_id)).all():
        if row.system_name in target_systems:
            db.delete(row)
            dropped += 1
        else:
            row.contact_id = target_id
            target_systems.add(row.system_name)
            repointed += 1
    return repointed, dropped


def _repoint_contact_tags(db: Session, *, duplicate_id: uuid.UUID, target_id: uuid.UUID) -> tuple[int, int]:
    """A tag already on the survivor is dropped from the duplicate rather
    than colliding on uq_contact_tag_contact_id_tag; the rest move over.
    """

    target_tags = {row.tag for row in db.scalars(select(ContactTag).where(ContactTag.contact_id == target_id)).all()}
    repointed = dropped = 0
    for row in db.scalars(select(ContactTag).where(ContactTag.contact_id == duplicate_id)).all():
        if row.tag in target_tags:
            db.delete(row)
            dropped += 1
        else:
            row.contact_id = target_id
            target_tags.add(row.tag)
            repointed += 1
    return repointed, dropped


def merge_contact(
    db: Session,
    *,
    contact: Contact,
    duplicate_of_contact_id: uuid.UUID,
    actor_id: uuid.UUID,
    acting_dealership_id: uuid.UUID | None,
    may_write_customer_role: bool = True,
) -> Contact:
    """Merges `contact` (the duplicate) into the survivor (FR-09).

    Re-points vehicle-party rows, phones, emails and external IDs at the
    survivor in the same transaction as the flag flip — a merge
    that only sets the flag silently orphans the survivor's 360 view from
    everything the duplicate used to carry, which the PRD's own Risks
    section calls "worse than no merge... a correctness bug, not an
    enhancement." Conflicting child rows (the same phone, email or
    external-id system already present on both) are dropped from the
    duplicate rather than erroring — the survivor's copy wins. A vehicle
    role+period present on both is the exception (KAN-177, ADR-064): the
    duplicate's copy is kept, closed and marked merged, never deleted — see
    _repoint_vehicle_parties.

    KAN-366: every refusal names a `details.reason`, so the merge dialog
    says it in the user's language instead of showing this English message.
    """

    if duplicate_of_contact_id == contact.id:
        raise BadRequestError("A contact cannot be merged into itself.", details={"reason": "merge_into_self"})

    target = _group_scoped_or_404(db, Contact, duplicate_of_contact_id, contact.group_id)
    # Read without a lock here; both statuses are checked again under the
    # contact-row locks the merge takes (KAN-372, _lock_merge_contacts).
    if target.lifecycle_status == ContactLifecycleStatus.MERGED:
        raise ConflictError(
            "Cannot merge into a contact that has itself been merged.",
            details={"reason": "merge_target_merged", "duplicateOfContactId": str(duplicate_of_contact_id)},
        )
    if contact.lifecycle_status == ContactLifecycleStatus.MERGED:
        raise ConflictError(
            "Contact has already been merged.",
            details={"reason": "contact_already_merged", "currentLifecycleStatus": "merged"},
        )

    before = {"lifecycleStatus": contact.lifecycle_status.value, "duplicateOfContactId": None}

    parties_repointed, parties_kept_closed = _repoint_vehicle_parties(
        db,
        duplicate_id=contact.id,
        target_id=target.id,
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        group_id=contact.group_id,
    )
    phones_repointed, phones_dropped = _repoint_contacts(
        db, ContactPhone, ("phone_e164",), duplicate_id=contact.id, target_id=target.id
    )
    emails_repointed, emails_dropped = _repoint_contacts(
        db, ContactEmail, ("email_address",), duplicate_id=contact.id, target_id=target.id
    )
    addresses_repointed, addresses_dropped = _repoint_contacts(
        db,
        ContactAddress,
        ("address_type", "address_street", "address_house_number", "address_postal_code", "address_country"),
        duplicate_id=contact.id,
        target_id=target.id,
    )
    external_ids_repointed, external_ids_dropped = _repoint_external_ids(
        db, duplicate_id=contact.id, target_id=target.id
    )
    tags_repointed, tags_dropped = _repoint_contact_tags(db, duplicate_id=contact.id, target_id=target.id)
    contact_groups_repointed, contact_groups_dropped = contact_groups_service.repoint_contact_groups(
        db, duplicate_id=contact.id, target_id=target.id
    )
    # KAN-464 Q3: a merge moves the customer role and its fields, so a
    # contact holding it is merged only by who may write that role. Judged
    # here, under both contact-row locks (taken with the vehicle parties
    # above), so a role given meanwhile is seen; a refusal rolls back the
    # whole merge.
    if not may_write_customer_role and roles_service.holds_customer_role_any(db, (contact.id, target.id)):
        raise roles_service.customer_role_not_permitted("merge")
    # KAN-464: the survivor holds every role either contact held.
    roles_gained, role_rows_moved = roles_service.repoint_roles(db, duplicate_id=contact.id, target_id=target.id)
    earlier_merges_repointed, party_copies_repointed = _repoint_earlier_merges(
        db,
        duplicate_id=contact.id,
        target_id=target.id,
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        group_id=contact.group_id,
    )

    contact.lifecycle_status = ContactLifecycleStatus.MERGED
    contact.duplicate_of_contact_id = duplicate_of_contact_id
    contact.updated_by = actor_id
    contact.version += 1

    record_audit_event(
        db,
        entity_type="contact",
        entity_id=contact.id,
        tenant_id=contact.group_id,
        action="merge",
        actor_id=actor_id,
        acting_dealership_id=acting_dealership_id,
        before=before,
        after={
            "lifecycleStatus": "merged",
            "duplicateOfContactId": str(duplicate_of_contact_id),
            "vehiclePartiesRepointed": parties_repointed,
            # KAN-177: the overlapping rows are kept, closed and marked, not dropped.
            "vehiclePartiesKeptClosed": len(parties_kept_closed),
            "vehiclePartiesKeptClosedIds": [str(party_id) for party_id in parties_kept_closed],
            "phonesRepointed": phones_repointed,
            "phonesDropped": phones_dropped,
            "emailsRepointed": emails_repointed,
            "emailsDropped": emails_dropped,
            "addressesRepointed": addresses_repointed,
            "addressesDropped": addresses_dropped,
            "externalIdsRepointed": external_ids_repointed,
            "externalIdsDropped": external_ids_dropped,
            "tagsRepointed": tags_repointed,
            "tagsDropped": tags_dropped,
            "contactGroupsRepointed": contact_groups_repointed,
            "contactGroupsDropped": contact_groups_dropped,
            "rolesGained": roles_gained,
            "roleRowsMoved": role_rows_moved,
            # KAN-383: contacts merged into this duplicate earlier now name the survivor.
            "earlierMergesRepointedIds": [str(contact_id) for contact_id in earlier_merges_repointed],
            # KAN-392: the vehicle-link copies those earlier merges kept, now naming the survivor.
            "vehiclePartyCopiesRepointedIds": [str(party_id) for party_id in party_copies_repointed],
        },
    )
    # KAN-185 (ADR-047) — Sales moves its records of the duplicate (offers,
    # contracts, deals) from this event, in its own transaction
    # (`sales.contact_merged`); the merge writes nothing of Sales'.
    publish_event(
        db,
        OutboxEvent(
            event_type="contact.merged",
            tenant_id=contact.group_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="contact",
            aggregate_id=contact.id,
            # Survivor's label fields, with contactId/duplicateOfContactId
            # overriding the payload helper's own contactId — a consumer
            # needs the duplicate's id (what it may still be pointing at)
            # and the survivor's id (what to repoint to) in the same event.
            payload={
                **_contact_label_payload(target),
                "contactId": str(contact.id),
                "duplicateOfContactId": str(duplicate_of_contact_id),
            },
        ),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Merge conflicts with an existing record on the survivor.",
            details={"reason": "merge_conflict_on_survivor"},
        ) from exc
    db.refresh(contact)
    return contact
