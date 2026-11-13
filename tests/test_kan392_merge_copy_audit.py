"""KAN-392 (Anto, 2026-10-31, option B): when D is merged into T, the
vehicle-link copies an earlier merge X→D kept on X (KAN-177, "merged into D")
are re-pointed to T (KAN-383). Each re-pointed copy leaves a History entry on
X, and the D→T merge's own audit lists the copies. Old chains are not
repaired."""

import datetime as dt
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.customer.models.vehicle_party import VehicleParty
from app.customer.services import customer as customer_service
from tests.test_customer_vehicle_party_close_rule import OWNER, _customer, _merge, _vehicle


def _two_step_chain(db_session, vin: str):
    """X and D both owned the car from the same day: merging X into D keeps
    X's row as a closed copy marked "merged into D"."""

    group_id = uuid.uuid4()
    car = _vehicle(db_session, vin)
    x, d, t = (_customer(db_session, group_id, name) for name in ("Xaver", "Dora", "Tina"))
    start = utcnow() - dt.timedelta(days=30)
    kept = VehicleParty(vehicle_id=car.id, customer_id=x.id, role=OWNER, effective_from=start)
    db_session.add_all([kept, VehicleParty(vehicle_id=car.id, customer_id=d.id, role=OWNER, effective_from=start)])
    db_session.commit()
    ids = SimpleNamespace(group=group_id, car=car.id, x=x.id, d=d.id, t=t.id, kept=kept.id)
    _merge(db_session, ids.x, ids.d, group_id)
    return ids


def _party_updates(db_session, customer_id):
    return db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_id == customer_id, AuditEvent.action == "vehicle_party_update")
        .order_by(AuditEvent.id)
    ).all()


def test_a_repointed_copy_is_audited_on_its_customer(db_session):
    ids = _two_step_chain(db_session, "WVWZZZ1KZAW392001")
    assert _party_updates(db_session, ids.x) == []

    _merge(db_session, ids.d, ids.t, ids.group)

    db_session.expire_all()
    assert db_session.get(VehicleParty, ids.kept).merged_into_customer_id == ids.t
    [update] = _party_updates(db_session, ids.x)
    assert update.entity_type == "customer"
    assert update.tenant_id == ids.group
    assert update.reason == "customer_merged"
    assert update.before == {
        "vehicleId": str(ids.car),
        "role": OWNER.value,
        "mergedIntoCustomerId": str(ids.d),
    }
    assert update.after == {"mergedIntoCustomerId": str(ids.t)}
    # The same actor as the merge that moved it.
    [merge] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == ids.d, AuditEvent.action == "merge")
    ).all()
    assert update.actor_id == merge.actor_id


def test_the_merge_audit_lists_the_repointed_copies(db_session):
    ids = _two_step_chain(db_session, "WVWZZZ1KZAW392002")
    [first] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == ids.x, AuditEvent.action == "merge")
    ).all()
    # X→D re-pointed no earlier merge's copy.
    assert first.after["vehiclePartyCopiesRepointedIds"] == []

    _merge(db_session, ids.d, ids.t, ids.group)

    [second] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == ids.d, AuditEvent.action == "merge")
    ).all()
    assert second.after["vehiclePartyCopiesRepointedIds"] == [str(ids.kept)]
    assert second.after["earlierMergesRepointedIds"] == [str(ids.x)]


def test_the_history_names_a_repointed_copy_by_its_vehicle(db_session):
    """The merge's `vehiclePartyCopiesRepointedIds` lists party rows; the
    History names each by its vehicle, from the row's own label (KAN-84)."""

    group_id = uuid.uuid4()
    car = _vehicle(db_session, "WVWZZZ1KZAW392003")
    ours = _customer(db_session, group_id, "Xaver")
    copy = VehicleParty(
        vehicle_id=car.id, customer_id=ours.id, role=OWNER, effective_from=utcnow(),
        vehicle_make="VW", vehicle_model="Golf", vehicle_model_year=1975,
    )
    db_session.add(copy)
    db_session.commit()
    event = SimpleNamespace(actor_id=None, before=None, after={"vehiclePartyCopiesRepointedIds": [str(copy.id)]})

    labels = customer_service.history_labels(db_session, group_id=group_id, dealership_id=uuid.uuid4(), events=[event])

    assert labels == {str(copy.id): "VW Golf (1975)"}
