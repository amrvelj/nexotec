"""VehicleParty reads (Contact PRD D-12, FR-10; split from contact.py,
KAN-617): a contact's vehicles, a vehicle's holders in the caller's group,
the batch reads Sales and the detail screens use.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, and_, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session, load_only

from app.contact.models.contact import (
    Contact,
    ContactLifecycleStatus,
)
from app.contact.models.vehicle_party import VehicleParty
from app.contact.schemas.contact import (
    OtherVehiclePartySummary,
)
from app.contact.services.contact_common import contact_display_name
from app.contact.services.vehicle_party_labels import _fill_unlabelled_vehicle_parties_for_read, _write_vehicle_labels
from app.core.base import utcnow
from app.core.errors import BadRequestError, NotFoundError

# --- VehicleParty: contact-to-vehicle relationships (Contact PRD D-12,
# FR-10). Vehicle is tenant-agnostic (no tenant_id — a VIN is global, see
# app/models/vehicle.py), so the tenant boundary here is enforced purely by
# resolving `contact` through get_contact_or_404 before any of these run;
# the vehicle_id itself is looked up with no tenant filter, same as every
# other vehicle lookup in the codebase.


def _validate_effective_range(effective_from, effective_to) -> None:
    # A client never writes a zero-length period. Such rows are written by
    # the server alone: the copy a contact merge keeps (KAN-177) and a
    # planned holder cancelled before its start (KAN-583, «planned, never
    # started»), both closed at their own start — so a PATCH of those dates
    # is refused here like any other empty period.
    if effective_to is not None and effective_to <= effective_from:
        raise BadRequestError(
            "effective_to must be after effective_from (a zero-length period is written only by the "
            "server: the copy a contact merge keeps, or an entry cancelled before it began)."
        )


def list_contact_vehicles(db: Session, *, contact_id: uuid.UUID, include_closed: bool = False) -> list[VehicleParty]:
    """Default is CURRENT allocations only (effective_to is null or still
    in the future) — WP-5 PR-9, ADR-064. include_closed=True is FR-V-16's
    "former allocations" view on Vehicle 360's Identity tab; the row
    itself is never deleted (see delete_contact_vehicle below), so
    history is always available on request, just not mixed into the
    default view by default.
    """

    stmt = (
        select(VehicleParty).where(VehicleParty.contact_id == contact_id).order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(_holds_now_or_later())
    parties = list(db.scalars(stmt).all())
    _fill_unlabelled_vehicle_parties_for_read(db, parties)
    return parties


def list_vehicle_parties(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[VehicleParty]:
    """The vehicle-side mirror of list_contact_vehicles — FR-V-16's
    Vehicle 360 Identity tab needs "who holds which role on THIS car",
    keyed by vehicle rather than by contact. Same default-open-only /
    include_closed=True shape.

    group_id is required, not optional, on purpose — same reasoning as
    list_other_vehicle_parties_batch just below: vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no
    group_id column at all, so two entirely unrelated dealer groups can
    genuinely attach a party row to the SAME vehicle_id. Without this
    join, one group's advisor would see another group's contact's id
    (and, via the Identity tab's contact overlay, their name) as a party
    on a car neither dealership has any relationship over — the exact
    cross-tenant leak rule #7 and ADR-049 forbid. A row whose contact
    belongs to another group (or is dangling) is silently dropped, never
    a 403/404 on the whole vehicle: this is a read-model projection of a
    globally-keyed fact, not a request FOR that other group's contact
    record.
    """

    stmt = _vehicle_parties_in_group_stmt(
        VehicleParty, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
    )
    return list(db.scalars(stmt).all())


def list_vehicle_party_holders(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[tuple[VehicleParty, str]]:
    """KAN-140 — list_vehicle_parties plus each holder's display name, for
    the Identity tab (an internal id is never user-visible text). Built
    from the SAME group-scoped statement, so a name can only come from a
    contact the row filter already admits: another group's holder is
    dropped before its name is ever read.
    """

    stmt = _vehicle_parties_in_group_stmt(
        VehicleParty, Contact, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
    ).options(
        # Only what contact_display_name reads — never the whole row (which
        # would decrypt tax_id just to build a label).
        load_only(Contact.company_name, Contact.first_name, Contact.last_name, Contact.contact_number)
    )
    return [(party, contact_display_name(contact)) for party, contact in db.execute(stmt).all()]


def _vehicle_parties_in_group_stmt(
    *entities: type[VehicleParty] | type[Contact], vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool
) -> Select:
    """The one group-scoped read behind list_vehicle_parties and
    list_vehicle_party_holders — see list_vehicle_parties for why the
    Contact join is the group boundary."""

    stmt = (
        select(*entities)
        .join(Contact, Contact.id == VehicleParty.contact_id)
        .where(VehicleParty.vehicle_id == vehicle_id, Contact.group_id == group_id)
        .order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(_holds_now_or_later())
    return stmt


def contact_in_group(
    contact_id: ColumnElement[Any] | InstrumentedAttribute[Any], group_id: ColumnElement[Any]
) -> Exists:
    """KAN-259 — a SQL predicate, for another context's nightly
    reconciliation (Sales'): the contact `contact_id` names belongs to
    group `group_id`. Contacts are group-scoped (ADR-014), so a deal's
    contact is right only in the group of the deal's dealership, which the
    caller passes as an expression. Builds an EXISTS; reads nothing itself."""

    return select(Contact.id).where(Contact.id == contact_id, Contact.group_id == group_id).exists()


def contact_is_merged(
    contact_id: ColumnElement[Any] | InstrumentedAttribute[Any], *, merged_before: dt.datetime
) -> Exists:
    """KAN-581 — a SQL predicate, for another context's nightly
    reconciliation (Sales'): the contact `contact_id` names is merged, and
    its row was last written before `merged_before`. The bound lets the
    caller leave a merge whose `contact.merged` is still within outbox lag
    alone. It is judged on `updated_at`, not on the merge's own time: a
    merged row can still be written after its merge (a credit-block change,
    KAN-383 re-pointing it when its survivor is merged in turn, the nightly
    advisor-label refresh), and each such write restarts the grace. A
    finding is then reported a night later, never lost. Builds an EXISTS;
    reads nothing itself. Confined to reconciliation modules
    (`test_contact_predicates_only_in_reconciliation.py`)."""

    return (
        select(Contact.id)
        .where(
            Contact.id == contact_id,
            Contact.lifecycle_status == ContactLifecycleStatus.MERGED,
            Contact.updated_at < merged_before,
        )
        .exists()
    )


@dataclass(frozen=True)
class ContactSalesLabel:
    """What Sales copies of a contact beside its `contact_id` (rule 2):
    the display name (`contact_display_name`) and the correspondence
    language. `merged` says the record was merged into another — Sales'
    merge consumer moves its rows to the survivor (KAN-185), so a label job
    leaves such a row as it is."""

    label: str
    language: str
    merged: bool


def get_contact_sales_labels(db: Session, contact_ids: set[uuid.UUID]) -> dict[uuid.UUID, ContactSalesLabel]:
    """KAN-590: the labels of `contact_ids`, for Sales' nightly
    `sales.contact_labels` job — one read per batch, never a join into this
    context's tables. An id that names no contact is absent from the answer.
    Read by id only: each id was resolved in its caller's group when Sales
    attached it."""

    if not contact_ids:
        return {}
    # Only what the label needs: a whole row would decrypt `tax_id` for every
    # contact on open work each night (as in list_vehicle_party_holders).
    rows = db.scalars(
        select(Contact)
        .options(
            load_only(
                Contact.company_name,
                Contact.first_name,
                Contact.last_name,
                Contact.contact_number,
                Contact.language,
                Contact.lifecycle_status,
            )
        )
        .where(Contact.id.in_(contact_ids))
    ).all()
    return {
        row.id: ContactSalesLabel(
            label=contact_display_name(row),
            language=row.language.value,
            merged=row.lifecycle_status == ContactLifecycleStatus.MERGED,
        )
        for row in rows
    }


def list_other_vehicle_parties_batch(
    db: Session, *, vehicle_ids: list[uuid.UUID], exclude_contact_id: uuid.UUID, group_id: uuid.UUID
) -> dict[uuid.UUID, list[OtherVehiclePartySummary]]:
    """KAN-49 / FR-19 — "who else is a party on the same car", for every
    vehicle on the Vehicles tab in ONE pass (2 queries total, never N+1
    regardless of how many vehicles the contact has). contact_id on
    VehicleParty is intra-context (contact -> contact), so this is a
    plain batched read, not the three-column denormalization pattern that
    rule #2/#3 reserve for cross-context references.

    Open parties only — exit criterion 1: a closed row is excluded from
    "others", it is not a party now (this contact's own closed rows are a
    separate, deliberately-included concern — see the /vehicles route).

    group_id is required, not optional, on purpose. vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no group_id
    column at all, so two entirely unrelated dealer groups can genuinely
    attach a party row to the SAME vehicle_id — without this filter, one
    group's advisor would see another group's contact's name and id as
    an "other party" on a car neither dealership actually shares a
    relationship over. Contacts ARE group-scoped (ADR-014), so the
    Contact half of this join is where the boundary has to be drawn.
    A contact from a different group is silently skipped, the same as
    the existing dangling-contact-id case below — never a 404 or an
    error, since this is a read-model nicety on someone else's own tab,
    not a request FOR that other contact's record.
    """

    if not vehicle_ids:
        return {}

    party_rows = list(
        db.scalars(
            select(VehicleParty)
            .where(
                VehicleParty.vehicle_id.in_(vehicle_ids),
                VehicleParty.contact_id != exclude_contact_id,
                _holds_now_or_later(),
            )
            .order_by(VehicleParty.role)
        ).all()
    )
    if not party_rows:
        return {}

    other_contact_ids = {p.contact_id for p in party_rows}
    contacts_by_id = {
        c.id: c
        for c in db.scalars(
            select(Contact).where(Contact.id.in_(other_contact_ids), Contact.group_id == group_id)
        ).all()
    }

    by_vehicle: dict[uuid.UUID, list[OtherVehiclePartySummary]] = {}
    for party in party_rows:
        other_contact = contacts_by_id.get(party.contact_id)
        # A dangling contact_id (deleted/merged elsewhere) is silently
        # skipped rather than rendering a broken party row — the write
        # path never leaves this dangling in practice, but a read-model
        # projection should never 500 on stale data.
        if other_contact is None:
            continue
        by_vehicle.setdefault(party.vehicle_id, []).append(
            OtherVehiclePartySummary(
                contact_id=party.contact_id,
                role=party.role,
                display_name=contact_display_name(other_contact),
                effective_from=party.effective_from,
            )
        )
    return by_vehicle


def get_contact_vehicle_or_404(db: Session, *, contact_id: uuid.UUID, party_id: uuid.UUID) -> VehicleParty:
    """Only the write routes (PATCH, DELETE) load a single party, so a row
    written before KAN-84 gets its vehicle label written here, committed by
    that write — the response is built after the caller's commit/refresh.
    The row is read unlocked; each write then locks and re-reads it
    (`_lock_party`, KAN-148), which labels it again after the re-read."""

    party = db.scalar(select(VehicleParty).where(VehicleParty.id == party_id, VehicleParty.contact_id == contact_id))
    if party is None:
        raise NotFoundError(f"VehicleParty {party_id} was not found.")
    if party.vehicle_label_refreshed_at is None:
        _write_vehicle_labels(db, [party])
    return party


def _holds_now_or_later(now: dt.datetime | None = None) -> ColumnElement[bool]:
    """A row that holds its role now or will (open, or ending later), as
    every «current» read and the incumbent lookup filter it. A row with an
    empty period — closed at its own start: a planned holder cancelled before
    it began (KAN-583, ruling 1A) or the copy a merge kept (KAN-177) — never
    holds the role, even while its start is still to come. `now` is the
    caller's clock when one decision spans several reads (KAN-664's
    Disconnect); without it, the current time."""

    return or_(
        VehicleParty.effective_to.is_(None),
        and_(
            VehicleParty.effective_to > (now if now is not None else utcnow()),
            VehicleParty.effective_to > VehicleParty.effective_from,
        ),
    )


def _is_open(effective_to: dt.datetime | None) -> bool:
    """A row holds its role now when it has no end, or an end still to come
    — the same test every read and the incumbent lookup apply."""

    return effective_to is None or effective_to > utcnow()
