"""The locks every VehicleParty writer takes, in order (KAN-99, KAN-356, KAN-365;
split from customer.py, KAN-617): the (vehicle, role, group) advisory lock,
then the customer row FOR SHARE (refusing a merged customer), then party rows.
"""

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.customer.models.customer import (
    Customer,
    CustomerLifecycleStatus,
)
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.vehicle_party_labels import _write_vehicle_labels


def _lock_role_rows(db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID) -> None:
    """KAN-583/KAN-644: every party row of (vehicle, role) in the group,
    locked FOR UPDATE in **id order**, in one statement — taken right after
    the (vehicle, role, group) advisory lock by a Disconnect, a PATCH and an
    allocation. A cancel or a moved planned start writes the planned row and
    the older row it links; locking them one by one (the newer first) would
    deadlock against the vehicle-party label refresh (KAN-186), which locks a
    vehicle's rows in id order. Later per-row locks in the same transaction
    then find their rows already held."""

    _lock_rows_of_roles(db, vehicle_id=vehicle_id, roles=(role,), group_id=group_id)


def _lock_rows_of_roles(
    db: Session, *, vehicle_id: uuid.UUID, roles: Iterable[VehiclePartyRole], group_id: uuid.UUID
) -> None:
    """`_lock_role_rows` for a writer of several roles at once (KAN-663: the
    trade-in purchase and its storno write owner and keeper): every row of
    those roles in **one** statement, in id order across the roles — locking
    them role by role would take a newer keeper row before an older owner row,
    out of the order the label refresh (KAN-186) and the nightly refresh take
    them. Taken after the roles' advisory locks and the customer row, as an
    allocation takes its rows."""

    db.execute(
        select(VehicleParty.id)
        .join(Customer, Customer.id == VehicleParty.customer_id)
        .where(
            VehicleParty.vehicle_id == vehicle_id,
            VehicleParty.role.in_(list(roles)),
            Customer.group_id == group_id,
        )
        .order_by(VehicleParty.id)
        .with_for_update(of=VehicleParty)
    ).all()


def _lock_party(db: Session, party: VehicleParty) -> VehicleParty:
    """KAN-148: the party row, locked FOR UPDATE and re-read, so a write
    decides on the committed row and not on what this session read before a
    concurrent Disconnect or allocation committed. The re-read discards any
    unflushed change on the row, so a row written before KAN-84 is labelled
    again here (see get_customer_vehicle_or_404)."""

    locked = db.scalar(
        select(VehicleParty)
        .where(VehicleParty.id == party.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:  # pragma: no cover - party rows are never deleted (ADR-064)
        raise NotFoundError(f"VehicleParty {party.id} was not found.")
    if locked.vehicle_label_refreshed_at is None:
        _write_vehicle_labels(db, [locked])
    return locked


def _refuse_merged_customer(db: Session, customer_id: uuid.UUID) -> None:
    """KAN-365: allocation and a reopen refuse a merged customer — the
    survivor carries its vehicle history (ADR-064, KAN-177), and an open row
    left on the merged record is invisible on the survivor's 360. A create
    with a past `effectiveTo` refuses it the same way (KAN-370). The caller
    holds the (vehicle, role, group) advisory lock; the customer row is read
    FOR SHARE under it, so an allocation racing the merge of this customer
    either commits before the merge reads the duplicate's pairs (the merge
    locks the row FOR NO KEY UPDATE first, see _repoint_vehicle_parties) or
    waits for the merge and reads the committed `merged` status here."""

    status, merged_into = db.execute(
        select(Customer.lifecycle_status, Customer.duplicate_of_customer_id)
        .where(Customer.id == customer_id)
        .with_for_update(read=True)
    ).one()
    if status == CustomerLifecycleStatus.MERGED:
        raise ConflictError(
            "This customer has been merged into another; give the vehicle role to the customer it was merged into.",
            details={
                "reason": "customer_merged",
                "mergedIntoCustomerId": str(merged_into) if merged_into else None,
            },
        )


def _lock_vehicle_role_in_group(
    db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID
) -> None:
    """pg_advisory_xact_lock on a 64-bit key derived from (vehicle, role,
    group); released by the transaction's commit or rollback. SQLite (the
    fast local lane, ADR-011) has no advisory locks and serialises
    writers on its own, so this is a no-op there.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _vehicle_role_lock_key(vehicle_id, role, group_id)})


def _try_lock_vehicle_role_in_group(
    db: Session, *, vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID
) -> bool:
    """The same lock, without waiting: True once held, False if another
    transaction holds it (KAN-356: a merge's out-of-order late pair). Always
    True on SQLite, as above."""

    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _vehicle_role_lock_key(vehicle_id, role, group_id)}
        ).scalar()
    )


def _vehicle_role_lock_key(vehicle_id: uuid.UUID, role: VehiclePartyRole, group_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"vehicle_party:{vehicle_id}:{role.value}:{group_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
