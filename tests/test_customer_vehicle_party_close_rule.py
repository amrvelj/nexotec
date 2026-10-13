"""KAN-148 (Anto ticked option A, 2026-10-12): no path to a vehicle party
bypasses ADR-064's close-the-previous-holder rule.

- A create whose `effectiveTo` is still to come is a holder now: it closes
  every other open holder of the role in the group, under the allocation's
  lock, and keeps its end date. A create whose `effectiveTo` is already past
  is history and closes nobody.
- A PATCH that reopens a closed row (`effectiveTo: null`, or an end still to
  come) closes every other open holder in the group the same way and
  publishes `customer.vehicle_party.linked`.
- Disconnect locks its row, so racing another Disconnect or an allocation it
  closes once and publishes one `unlinked` event.
"""

import datetime as dt
import threading
import time
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.schemas.customer import CustomerVehicleCreate, CustomerVehicleUpdate
from app.customer.services import customer as customer_service
from app.customer.services.customer import (
    allocate_vehicle_party,
    create_customer_vehicle,
    delete_customer_vehicle,
    get_customer_vehicle_or_404,
    update_customer_vehicle,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

OWNER = VehiclePartyRole.OWNER


def _customer(db_session, group_id: uuid.UUID, first_name: str) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Muster",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def _vehicle(db_session, vin: str):
    vehicle = create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
    db_session.commit()
    return vehicle


def _allocate(db, vehicle_id, customer: Customer | uuid.UUID, group_id, role=OWNER) -> VehicleParty:
    customer_id = customer if isinstance(customer, uuid.UUID) else customer.id
    return allocate_vehicle_party(
        db, vehicle_id=vehicle_id, customer_id=customer_id, role=role, group_id=group_id, actor_id=uuid.uuid4(),
    )


def _open_holders(db_session, vehicle_id, role=OWNER) -> set[uuid.UUID]:
    db_session.expire_all()
    rows = db_session.scalars(
        select(VehicleParty).where(VehicleParty.vehicle_id == vehicle_id, VehicleParty.role == role)
    ).all()
    return {r.customer_id for r in rows if r.effective_to is None or r.effective_to > utcnow()}


def _events(db_session, event_type: str, aggregate_id: uuid.UUID | None = None) -> list[OutboxMessage]:
    stmt = select(OutboxMessage).where(OutboxMessage.event_type == event_type)
    if aggregate_id is not None:
        stmt = stmt.where(OutboxMessage.aggregate_id == aggregate_id)
    return list(db_session.scalars(stmt).all())


def _patch(db, party_ref: tuple[uuid.UUID, uuid.UUID], group_id, **fields) -> VehicleParty:
    customer_id, party_id = party_ref
    loaded = get_customer_vehicle_or_404(db, customer_id=customer_id, party_id=party_id)
    return update_customer_vehicle(
        db, party=loaded, data=CustomerVehicleUpdate(**fields), actor_id=uuid.uuid4(), group_id=group_id,
    )


def _postgres_only(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("row and advisory locks are Postgres-only; the SQLite lane serialises writers itself")


# --- create with an end date ---------------------------------------------------


def test_a_create_with_a_future_end_date_closes_the_previous_holder(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148001")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    alice_party = _allocate(db_session, vehicle.id, alice, group_id)
    until = utcnow() + dt.timedelta(days=90)

    bruno_party = create_customer_vehicle(
        db_session, customer=bruno, data=CustomerVehicleCreate(vehicle_id=vehicle.id, role=OWNER, effective_to=until),
        actor_id=uuid.uuid4(),
    )

    assert _open_holders(db_session, vehicle.id) == {bruno.id}
    db_session.refresh(bruno_party)
    assert bruno_party.effective_to == until  # the end date is kept
    assert len(_events(db_session, "customer.vehicle_party.unlinked", alice_party.id)) == 1
    assert len(_events(db_session, "customer.vehicle_party.linked", bruno_party.id)) == 1


def test_a_create_with_a_future_end_date_for_the_current_holder_replaces_the_open_ended_row(db_session):
    """The incumbent's no-op is for an identical, open-ended allocation only:
    an end date it lacks is a new statement, so the open-ended row closes."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148002")
    alice = _customer(db_session, group_id, "Alice")
    first = _allocate(db_session, vehicle.id, alice, group_id)

    second = create_customer_vehicle(
        db_session, customer=alice,
        data=CustomerVehicleCreate(vehicle_id=vehicle.id, role=OWNER, effective_to=utcnow() + dt.timedelta(days=30)),
        actor_id=uuid.uuid4(),
    )

    assert second.id != first.id
    db_session.refresh(first)
    assert first.effective_to is not None and first.effective_to <= utcnow()
    open_rows = [
        r for r in db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_id == vehicle.id)).all()
        if r.effective_to is None or r.effective_to > utcnow()
    ]
    assert [r.id for r in open_rows] == [second.id]


def test_a_create_repeating_a_rows_exact_start_is_a_409_and_changes_nothing(db_session):
    """uq_vehicle_party_scope: the same customer, role and effectiveFrom twice.
    Now that a future end date takes the allocation path, its insert turns
    the violation into the 409 the raw path gave — never a 500 — and the
    incumbent's close rolls back with it."""

    from app.core.errors import ConflictError

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148015")
    alice = _customer(db_session, group_id, "Alice")
    start = utcnow() - dt.timedelta(days=3)
    first = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=OWNER, group_id=group_id,
        actor_id=uuid.uuid4(), effective_from=start,
    )

    with pytest.raises(ConflictError):
        create_customer_vehicle(
            db_session, customer=alice,
            data=CustomerVehicleCreate(
                vehicle_id=vehicle.id, role=OWNER, effective_from=start, effective_to=utcnow() + dt.timedelta(days=9),
            ),
            actor_id=uuid.uuid4(),
        )

    db_session.expire_all()
    assert db_session.get(VehicleParty, first.id).effective_to is None
    assert _events(db_session, "customer.vehicle_party.unlinked") == []


def test_a_create_with_a_past_end_date_is_history_and_closes_nobody(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148003")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    _allocate(db_session, vehicle.id, alice, group_id)

    create_customer_vehicle(
        db_session, customer=bruno,
        data=CustomerVehicleCreate(
            vehicle_id=vehicle.id, role=OWNER,
            effective_from=utcnow() - dt.timedelta(days=400), effective_to=utcnow() - dt.timedelta(days=300),
        ),
        actor_id=uuid.uuid4(),
    )

    assert _open_holders(db_session, vehicle.id) == {alice.id}
    assert _events(db_session, "customer.vehicle_party.unlinked") == []


def test_a_create_with_a_future_end_date_never_closes_another_groups_holder(db_session):
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148004")
    alice, bruno = _customer(db_session, group_a, "Alice"), _customer(db_session, group_b, "Bruno")
    _allocate(db_session, vehicle.id, alice, group_a)

    create_customer_vehicle(
        db_session, customer=bruno,
        data=CustomerVehicleCreate(vehicle_id=vehicle.id, role=OWNER, effective_to=utcnow() + dt.timedelta(days=5)),
        actor_id=uuid.uuid4(),
    )

    assert _open_holders(db_session, vehicle.id) == {alice.id, bruno.id}
    assert _events(db_session, "customer.vehicle_party.unlinked") == []


# --- reopen by PATCH -------------------------------------------------------------


@pytest.mark.parametrize("reopen_to", [None, "future"], ids=["open-ended", "future-end"])
def test_reopening_a_closed_row_closes_the_current_holder(db_session, reopen_to):
    """Undoing a Disconnect clicked by mistake: the row is the holder again
    and whoever took the role meanwhile is closed — never two current owners."""

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148005")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    alice_party = _allocate(db_session, vehicle.id, alice, group_id)
    bruno_party = _allocate(db_session, vehicle.id, bruno, group_id)  # closes Alice
    effective_to = None if reopen_to is None else utcnow() + dt.timedelta(days=60)

    reopened = _patch(db_session, (alice_party.customer_id, alice_party.id), group_id, effective_to=effective_to)

    assert reopened.effective_to == effective_to
    assert _open_holders(db_session, vehicle.id) == {alice.id}
    assert len(_events(db_session, "customer.vehicle_party.unlinked", bruno_party.id)) == 1
    # Alice's row: linked on allocation, linked again on the reopen.
    assert len(_events(db_session, "customer.vehicle_party.linked", alice_party.id)) == 2


def test_a_patch_that_keeps_an_open_row_open_publishes_no_second_linked_event(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148006")
    alice = _customer(db_session, group_id, "Alice")
    party = _allocate(db_session, vehicle.id, alice, group_id)

    _patch(db_session, (party.customer_id, party.id), group_id, effective_to=utcnow() + dt.timedelta(days=10))

    assert len(_events(db_session, "customer.vehicle_party.linked", party.id)) == 1
    assert _open_holders(db_session, vehicle.id) == {alice.id}


def test_a_patch_that_ends_a_row_closes_nobody_else(db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148007")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    alice_party = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=OWNER, group_id=group_id,
        actor_id=uuid.uuid4(), effective_from=utcnow() - dt.timedelta(days=30),
    )
    _allocate(db_session, vehicle.id, bruno, group_id, role=VehiclePartyRole.KEEPER)

    _patch(db_session, (alice_party.customer_id, alice_party.id), group_id, effective_to=utcnow() - dt.timedelta(seconds=1))

    assert _open_holders(db_session, vehicle.id) == set()
    assert _open_holders(db_session, vehicle.id, VehiclePartyRole.KEEPER) == {bruno.id}


def test_reopening_never_closes_another_groups_holder(db_session):
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148008")
    alice, anna = _customer(db_session, group_a, "Alice"), _customer(db_session, group_a, "Anna")
    bruno = _customer(db_session, group_b, "Bruno")
    alice_party = _allocate(db_session, vehicle.id, alice, group_a)
    _allocate(db_session, vehicle.id, anna, group_a)  # closes Alice
    _allocate(db_session, vehicle.id, bruno, group_b)

    _patch(db_session, (alice_party.customer_id, alice_party.id), group_a, effective_to=None)

    assert _open_holders(db_session, vehicle.id) == {alice.id, bruno.id}


@pytest.mark.parametrize("reopen_to", [None, "future"], ids=["open-ended", "future-end"])
def test_the_copy_a_customer_merge_kept_is_never_reopened(db_session, reopen_to):
    """KAN-177 keeps the duplicate's overlapping row closed at its start and
    marked merged; a reopen would revive it and close the survivor's row."""

    from app.core.errors import ConflictError

    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148014")
    duplicate, survivor = _customer(db_session, group_id, "Dupli"), _customer(db_session, group_id, "Survi")
    survivor_party = _allocate(db_session, vehicle.id, survivor, group_id)
    kept = VehicleParty(
        vehicle_id=vehicle.id, customer_id=duplicate.id, role=OWNER,
        effective_from=utcnow() - dt.timedelta(days=10), merged_into_customer_id=survivor.id,
    )
    kept.effective_to = kept.effective_from
    db_session.add(kept)
    db_session.commit()
    effective_to = None if reopen_to is None else utcnow() + dt.timedelta(days=5)

    with pytest.raises(ConflictError) as refused:
        _patch(db_session, (duplicate.id, kept.id), group_id, effective_to=effective_to)

    assert refused.value.details["reason"] == "merged_party_row"
    db_session.rollback()
    assert _open_holders(db_session, vehicle.id) == {survivor.id}
    assert _events(db_session, "customer.vehicle_party.unlinked", survivor_party.id) == []


def _sales_headers(group_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id, roles=frozenset({AccessRole.SALES}),
    )
    return {"Authorization": f"Bearer {token}"}


def test_api_reopen_by_patch_closes_the_current_holder(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148009")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    alice_party = _allocate(db_session, vehicle.id, alice, group_id)
    _allocate(db_session, vehicle.id, bruno, group_id)

    response = client.patch(
        f"/v1/customers/{alice.id}/vehicles/{alice_party.id}",
        json={"effectiveTo": None},
        headers=_sales_headers(group_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["effectiveTo"] is None
    assert _open_holders(db_session, vehicle.id) == {alice.id}


def test_api_create_with_a_future_end_date_closes_the_current_holder(client, db_session):
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148010")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    _allocate(db_session, vehicle.id, alice, group_id)
    until = (utcnow() + dt.timedelta(days=30)).replace(microsecond=0)

    response = client.post(
        f"/v1/customers/{bruno.id}/vehicles",
        json={"vehicleId": str(vehicle.id), "role": "owner", "effectiveTo": until.isoformat()},
        headers=_sales_headers(group_id),
    )

    assert response.status_code == 201, response.text
    assert dt.datetime.fromisoformat(response.json()["effectiveTo"]) == until
    assert _open_holders(db_session, vehicle.id) == {bruno.id}


# --- Disconnect locks its row ----------------------------------------------------


def test_two_disconnects_of_one_row_close_it_once(engine, db_session):
    """Both requests read the row open; the second waits for the first's
    lock, re-reads it closed, and returns — one close, one `unlinked`."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148011")
    alice = _customer(db_session, group_id, "Alice")
    party = _allocate(db_session, vehicle.id, alice, group_id)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as first, factory() as second:
        in_first = get_customer_vehicle_or_404(first, customer_id=alice.id, party_id=party.id)
        in_second = get_customer_vehicle_or_404(second, customer_id=alice.id, party_id=party.id)
        delete_customer_vehicle(first, party=in_first, actor_id=uuid.uuid4(), group_id=group_id)
        closed_at = in_first.effective_to
        delete_customer_vehicle(second, party=in_second, actor_id=uuid.uuid4(), group_id=group_id)
        second.rollback()

    db_session.expire_all()
    assert db_session.get(VehicleParty, party.id).effective_to == closed_at
    assert len(_events(db_session, "customer.vehicle_party.unlinked", party.id)) == 1


def _pause_thread_after_closing(monkeypatch, thread_name: str):
    """Pauses `thread_name` at its new holder's audit row — after it has
    closed (and locked) the incumbents, before it commits."""

    reached, release = threading.Event(), threading.Event()
    real_record = customer_service.record_audit_event

    def _pause(db, **kwargs):
        if kwargs["action"] == "vehicle_party_add" and threading.current_thread().name == thread_name:
            reached.set()
            release.wait(timeout=10)
        return real_record(db, **kwargs)

    monkeypatch.setattr(customer_service, "record_audit_event", _pause)
    return reached, release


def _wait_until_a_backend_waits_on_a_lock(engine, timeout: float = 10.0) -> None:
    """Returns once some session is blocked on a lock (pg_locks, not
    granted) — so a race test really exercises the contended path instead
    of hoping a sleep was long enough."""

    deadline = time.monotonic() + timeout
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            if conn.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar():
                return
            conn.rollback()
            time.sleep(0.02)
    raise AssertionError("no session ever waited on a lock — the race was not exercised")


def _run(name: str, errors: list[Exception], target) -> threading.Thread:
    def _body():
        try:
            target()
        except Exception as exc:  # noqa: BLE001 — collected and asserted empty, never swallowed
            errors.append(exc)

    thread = threading.Thread(target=_body, name=name)
    thread.start()
    return thread


def test_a_disconnect_racing_an_allocation_closes_the_row_once(engine, db_session, monkeypatch):
    """The allocation closes Alice's row and is paused before committing; a
    Disconnect of the same row, read while it was still open, waits for the
    allocation's lock, finds the row closed and writes nothing."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148012")
    alice, bruno = _customer(db_session, group_id, "Alice"), _customer(db_session, group_id, "Bruno")
    db_session.commit()
    alice_party = _allocate(db_session, vehicle.id, alice, group_id)
    vehicle_id, bruno_id = vehicle.id, bruno.id  # plain values: the threads never touch db_session
    reached, release = _pause_thread_after_closing(monkeypatch, "allocation")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _allocation():
        with factory() as session:
            _allocate(session, vehicle_id, bruno_id, group_id)

    disconnect_session: Session = factory()
    try:
        stale = get_customer_vehicle_or_404(disconnect_session, customer_id=alice.id, party_id=alice_party.id)
        disconnect_session.commit()  # end the read; the row is still open in memory
        allocation = _run("allocation", errors, _allocation)
        assert reached.wait(timeout=10), "the allocation never reached its insert"

        def _disconnect():
            delete_customer_vehicle(disconnect_session, party=stale, actor_id=uuid.uuid4(), group_id=group_id)
            disconnect_session.rollback()

        disconnect = _run("disconnect", errors, _disconnect)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the Disconnect waits on the allocation's row lock
        release.set()
        allocation.join(timeout=10)
        disconnect.join(timeout=10)
    finally:
        release.set()
        disconnect_session.close()

    assert not allocation.is_alive() and not disconnect.is_alive()
    assert errors == []
    assert _open_holders(db_session, vehicle.id) == {bruno.id}
    assert len(_events(db_session, "customer.vehicle_party.unlinked", alice_party.id)) == 1


def test_a_reopen_racing_an_allocation_leaves_one_open_holder(engine, db_session, monkeypatch):
    """The reopen waits on the allocation's advisory lock, then sees the
    holder it inserted and closes it. Without the lock it would not see the
    uncommitted row and the group would end with two current owners."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    vehicle = _vehicle(db_session, "WVWZZZ1KZAW148013")
    alice, bruno, carla = (_customer(db_session, group_id, n) for n in ("Alice", "Bruno", "Carla"))
    db_session.commit()
    alice_party = _allocate(db_session, vehicle.id, alice, group_id)
    _allocate(db_session, vehicle.id, bruno, group_id)  # closes Alice
    alice_ref = (alice.id, alice_party.id)
    vehicle_id, carla_id = vehicle.id, carla.id  # plain values: the threads never touch db_session
    reached, release = _pause_thread_after_closing(monkeypatch, "allocation")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _allocation():
        with factory() as session:
            _allocate(session, vehicle_id, carla_id, group_id)

    def _reopen():
        with factory() as session:
            _patch(session, alice_ref, group_id, effective_to=None)

    allocation = _run("allocation", errors, _allocation)
    try:
        assert reached.wait(timeout=10), "the allocation never reached its insert"
        reopen = _run("reopen", errors, _reopen)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the reopen waits on the allocation's advisory lock
        release.set()
        allocation.join(timeout=10)
        reopen.join(timeout=10)
    finally:
        release.set()

    assert not allocation.is_alive() and not reopen.is_alive()
    assert errors == []
    assert _open_holders(db_session, vehicle.id) == {alice.id}
