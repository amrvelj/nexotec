"""VehicleParty reads (Customer PRD D-12, FR-10; split from customer.py,
KAN-617): a customer's vehicles, a vehicle's holders in the caller's group,
the batch reads Sales and the detail screens use.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, and_, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session, load_only

from app.core.base import utcnow
from app.core.errors import BadRequestError, NotFoundError
from app.customer.models.customer import (
    Customer,
    CustomerLifecycleStatus,
)
from app.customer.models.vehicle_party import VehicleParty
from app.customer.schemas.customer import (
    OtherVehiclePartySummary,
)
from app.customer.services.customer_common import customer_display_name
from app.customer.services.vehicle_party_labels import _fill_unlabelled_vehicle_parties_for_read, _write_vehicle_labels

# --- VehicleParty: customer-to-vehicle relationships (Customer PRD D-12,
# FR-10). Vehicle is tenant-agnostic (no tenant_id — a VIN is global, see
# app/models/vehicle.py), so the tenant boundary here is enforced purely by
# resolving `customer` through get_customer_or_404 before any of these run;
# the vehicle_id itself is looked up with no tenant filter, same as every
# other vehicle lookup in the codebase.


def _validate_effective_range(effective_from, effective_to) -> None:
    # A client never writes a zero-length period. Such rows are written by
    # the server alone: the copy a customer merge keeps (KAN-177) and a
    # planned holder cancelled before its start (KAN-583, «planned, never
    # started»), both closed at their own start — so a PATCH of those dates
    # is refused here like any other empty period.
    if effective_to is not None and effective_to <= effective_from:
        raise BadRequestError(
            "effective_to must be after effective_from (a zero-length period is written only by the "
            "server: the copy a customer merge keeps, or an entry cancelled before it began)."
        )


def list_customer_vehicles(db: Session, *, customer_id: uuid.UUID, include_closed: bool = False) -> list[VehicleParty]:
    """Default is CURRENT allocations only (effective_to is null or still
    in the future) — WP-5 PR-9, ADR-064. include_closed=True is FR-V-16's
    "former allocations" view on Vehicle 360's Identity tab; the row
    itself is never deleted (see delete_customer_vehicle below), so
    history is always available on request, just not mixed into the
    default view by default.
    """

    stmt = (
        select(VehicleParty).where(VehicleParty.customer_id == customer_id).order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(_holds_now_or_later())
    parties = list(db.scalars(stmt).all())
    _fill_unlabelled_vehicle_parties_for_read(db, parties)
    return parties


def list_vehicle_parties(
    db: Session, *, vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool = False
) -> list[VehicleParty]:
    """The vehicle-side mirror of list_customer_vehicles — FR-V-16's
    Vehicle 360 Identity tab needs "who holds which role on THIS car",
    keyed by vehicle rather than by customer. Same default-open-only /
    include_closed=True shape.

    group_id is required, not optional, on purpose — same reasoning as
    list_other_vehicle_parties_batch just below: vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no
    group_id column at all, so two entirely unrelated dealer groups can
    genuinely attach a party row to the SAME vehicle_id. Without this
    join, one group's advisor would see another group's customer's id
    (and, via the Identity tab's customer overlay, their name) as a party
    on a car neither dealership has any relationship over — the exact
    cross-tenant leak rule #7 and ADR-049 forbid. A row whose customer
    belongs to another group (or is dangling) is silently dropped, never
    a 403/404 on the whole vehicle: this is a read-model projection of a
    globally-keyed fact, not a request FOR that other group's customer
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
    customer the row filter already admits: another group's holder is
    dropped before its name is ever read.
    """

    stmt = _vehicle_parties_in_group_stmt(
        VehicleParty, Customer, vehicle_id=vehicle_id, group_id=group_id, include_closed=include_closed
    ).options(
        # Only what customer_display_name reads — never the whole row (which
        # would decrypt tax_id just to build a label).
        load_only(Customer.company_name, Customer.first_name, Customer.last_name, Customer.customer_number)
    )
    return [(party, customer_display_name(customer)) for party, customer in db.execute(stmt).all()]


def _vehicle_parties_in_group_stmt(
    *entities: type[VehicleParty] | type[Customer], vehicle_id: uuid.UUID, group_id: uuid.UUID, include_closed: bool
) -> Select:
    """The one group-scoped read behind list_vehicle_parties and
    list_vehicle_party_holders — see list_vehicle_parties for why the
    Customer join is the group boundary."""

    stmt = (
        select(*entities)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(VehicleParty.vehicle_id == vehicle_id, Customer.group_id == group_id)
        .order_by(VehicleParty.effective_from.desc())
    )
    if not include_closed:
        stmt = stmt.where(_holds_now_or_later())
    return stmt


def customer_in_group(
    customer_id: ColumnElement[Any] | InstrumentedAttribute[Any], group_id: ColumnElement[Any]
) -> Exists:
    """KAN-259 — a SQL predicate, for another context's nightly
    reconciliation (Sales'): the customer `customer_id` names belongs to
    group `group_id`. Customers are group-scoped (ADR-014), so a deal's
    customer is right only in the group of the deal's dealership, which the
    caller passes as an expression. Builds an EXISTS; reads nothing itself."""

    return select(Customer.id).where(Customer.id == customer_id, Customer.group_id == group_id).exists()


def customer_is_merged(
    customer_id: ColumnElement[Any] | InstrumentedAttribute[Any], *, merged_before: dt.datetime
) -> Exists:
    """KAN-581 — a SQL predicate, for another context's nightly
    reconciliation (Sales'): the customer `customer_id` names is merged, and
    its row was last written before `merged_before`. The bound lets the
    caller leave a merge whose `customer.merged` is still within outbox lag
    alone. It is judged on `updated_at`, not on the merge's own time: a
    merged row can still be written after its merge (a credit-block change,
    KAN-383 re-pointing it when its survivor is merged in turn, the nightly
    advisor-label refresh), and each such write restarts the grace. A
    finding is then reported a night later, never lost. Builds an EXISTS;
    reads nothing itself. Confined to reconciliation modules
    (`test_customer_predicates_only_in_reconciliation.py`)."""

    return (
        select(Customer.id)
        .where(
            Customer.id == customer_id,
            Customer.lifecycle_status == CustomerLifecycleStatus.MERGED,
            Customer.updated_at < merged_before,
        )
        .exists()
    )


@dataclass(frozen=True)
class CustomerSalesLabel:
    """What Sales copies of a customer beside its `customer_id` (rule 2):
    the display name (`customer_display_name`) and the correspondence
    language. `merged` says the record was merged into another — Sales'
    merge consumer moves its rows to the survivor (KAN-185), so a label job
    leaves such a row as it is."""

    label: str
    language: str
    merged: bool


def get_customer_sales_labels(db: Session, customer_ids: set[uuid.UUID]) -> dict[uuid.UUID, CustomerSalesLabel]:
    """KAN-590: the labels of `customer_ids`, for Sales' nightly
    `sales.customer_labels` job — one read per batch, never a join into this
    context's tables. An id that names no customer is absent from the answer.
    Read by id only: each id was resolved in its caller's group when Sales
    attached it."""

    if not customer_ids:
        return {}
    # Only what the label needs: a whole row would decrypt `tax_id` for every
    # customer on open work each night (as in list_vehicle_party_holders).
    rows = db.scalars(
        select(Customer)
        .options(
            load_only(
                Customer.company_name,
                Customer.first_name,
                Customer.last_name,
                Customer.customer_number,
                Customer.language,
                Customer.lifecycle_status,
            )
        )
        .where(Customer.id.in_(customer_ids))
    ).all()
    return {
        row.id: CustomerSalesLabel(
            label=customer_display_name(row),
            language=row.language.value,
            merged=row.lifecycle_status == CustomerLifecycleStatus.MERGED,
        )
        for row in rows
    }


def list_other_vehicle_parties_batch(
    db: Session, *, vehicle_ids: list[uuid.UUID], exclude_customer_id: uuid.UUID, group_id: uuid.UUID
) -> dict[uuid.UUID, list[OtherVehiclePartySummary]]:
    """KAN-49 / FR-19 — "who else is a party on the same car", for every
    vehicle on the Vehicles tab in ONE pass (2 queries total, never N+1
    regardless of how many vehicles the customer has). customer_id on
    VehicleParty is intra-context (customer -> customer), so this is a
    plain batched read, not the three-column denormalization pattern that
    rule #2/#3 reserve for cross-context references.

    Open parties only — exit criterion 1: a closed row is excluded from
    "others", it is not a party now (this customer's own closed rows are a
    separate, deliberately-included concern — see the /vehicles route).

    group_id is required, not optional, on purpose. vehicle_mdm is a
    deliberately global fact (ADR-022) and VehicleParty carries no group_id
    column at all, so two entirely unrelated dealer groups can genuinely
    attach a party row to the SAME vehicle_id — without this filter, one
    group's advisor would see another group's customer's name and id as
    an "other party" on a car neither dealership actually shares a
    relationship over. Customers ARE group-scoped (ADR-014), so the
    Customer half of this join is where the boundary has to be drawn.
    A customer from a different group is silently skipped, the same as
    the existing dangling-customer-id case below — never a 404 or an
    error, since this is a read-model nicety on someone else's own tab,
    not a request FOR that other customer's record.
    """

    if not vehicle_ids:
        return {}

    party_rows = list(
        db.scalars(
            select(VehicleParty)
            .where(
                VehicleParty.vehicle_id.in_(vehicle_ids),
                VehicleParty.customer_id != exclude_customer_id,
                _holds_now_or_later(),
            )
            .order_by(VehicleParty.role)
        ).all()
    )
    if not party_rows:
        return {}

    other_customer_ids = {p.customer_id for p in party_rows}
    customers_by_id = {
        c.id: c
        for c in db.scalars(
            select(Customer).where(Customer.id.in_(other_customer_ids), Customer.group_id == group_id)
        ).all()
    }

    by_vehicle: dict[uuid.UUID, list[OtherVehiclePartySummary]] = {}
    for party in party_rows:
        other_customer = customers_by_id.get(party.customer_id)
        # A dangling customer_id (deleted/merged elsewhere) is silently
        # skipped rather than rendering a broken party row — the write
        # path never leaves this dangling in practice, but a read-model
        # projection should never 500 on stale data.
        if other_customer is None:
            continue
        by_vehicle.setdefault(party.vehicle_id, []).append(
            OtherVehiclePartySummary(
                customer_id=party.customer_id,
                role=party.role,
                display_name=customer_display_name(other_customer),
                effective_from=party.effective_from,
            )
        )
    return by_vehicle


def get_customer_vehicle_or_404(db: Session, *, customer_id: uuid.UUID, party_id: uuid.UUID) -> VehicleParty:
    """Only the write routes (PATCH, DELETE) load a single party, so a row
    written before KAN-84 gets its vehicle label written here, committed by
    that write — the response is built after the caller's commit/refresh.
    The row is read unlocked; each write then locks and re-reads it
    (`_lock_party`, KAN-148), which labels it again after the re-read."""

    party = db.scalar(select(VehicleParty).where(VehicleParty.id == party_id, VehicleParty.customer_id == customer_id))
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
