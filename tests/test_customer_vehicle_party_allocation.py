"""WP-5 PR-9, ADR-064: setting a new holder for a role CLOSES the previous
one rather than overwriting it — never an update, never a silent
overwrite, never a delete.
"""

import datetime as dt
import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.errors import NotFoundError
from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.schemas.customer import VehiclePartySummary
from app.customer.services import customer as customer_service
from app.customer.services.customer import (
    allocate_vehicle_party,
    delete_customer_vehicle,
    list_customer_vehicles,
    list_vehicle_parties,
)
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

GROUP_ID = uuid.uuid4()


def _customer(db_session, first_name="Ada") -> Customer:
    customer = Customer(
        group_id=GROUP_ID, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Lovelace",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def _vehicle(db_session, vin="ZAR94000007123456"):
    return create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)


def _catalogue_variant(db_session) -> ModelVariant:
    brand = Brand(code=f"alfa-{uuid.uuid4().hex[:6]}", display_name="Alfa Romeo")
    db_session.add(brand)
    db_session.flush()
    model_group = ModelGroup(brand_id=brand.id, name="Giulietta")
    db_session.add(model_group)
    db_session.flush()
    variant = ModelVariant(model_group_id=model_group.id, name="1.4 TB Progression", model_year_from=2016, model_year_to=2020)
    db_session.add(variant)
    db_session.flush()
    return variant


def test_allocating_a_new_holder_closes_the_previous_one(db_session):
    vehicle = _vehicle(db_session)
    alice = _customer(db_session, "Alice")
    bob = _customer(db_session, "Bob")

    first = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    assert first.effective_to is None

    second = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=bob.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    assert second.effective_to is None
    assert second.customer_id == bob.id

    db_session.refresh(first)
    assert first.effective_to is not None  # closed, never overwritten
    assert first.customer_id == alice.id  # the row itself is untouched


def test_reallocating_the_same_customer_is_a_no_op(db_session):
    vehicle = _vehicle(db_session)
    alice = _customer(db_session, "Alice")

    first = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.KEEPER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    second = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.KEEPER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    assert first.id == second.id

    open_rows = list_customer_vehicles(db_session, customer_id=alice.id)
    assert len(open_rows) == 1


def test_delete_closes_never_deletes_and_is_idempotent(db_session):
    vehicle = _vehicle(db_session)
    alice = _customer(db_session, "Alice")
    party = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.DRIVER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    delete_customer_vehicle(db_session, party=party, actor_id=uuid.uuid4(), group_id=GROUP_ID)
    db_session.refresh(party)
    first_close_time = party.effective_to
    assert first_close_time is not None

    from app.customer.models.vehicle_party import VehicleParty

    still_there = db_session.scalar(select(VehicleParty).where(VehicleParty.id == party.id))
    assert still_there is not None  # never deleted

    # Idempotent: a second close doesn't move the timestamp.
    delete_customer_vehicle(db_session, party=party, actor_id=uuid.uuid4(), group_id=GROUP_ID)
    db_session.refresh(party)
    assert party.effective_to == first_close_time


def test_list_default_excludes_closed_include_closed_shows_history(db_session):
    vehicle = _vehicle(db_session)
    alice = _customer(db_session, "Alice")
    party = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    delete_customer_vehicle(db_session, party=party, actor_id=uuid.uuid4(), group_id=GROUP_ID)

    assert list_customer_vehicles(db_session, customer_id=alice.id) == []
    assert len(list_customer_vehicles(db_session, customer_id=alice.id, include_closed=True)) == 1


def test_allocation_publishes_linked_and_unlinked_outbox_events(db_session):
    vehicle = _vehicle(db_session)
    alice = _customer(db_session, "Alice")
    bob = _customer(db_session, "Bob")

    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=bob.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    events = list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type.in_(
                ["customer.vehicle_party.linked", "customer.vehicle_party.unlinked"]
            ))
        ).all()
    )
    event_types = [e.event_type for e in events]
    assert event_types.count("customer.vehicle_party.linked") == 2
    assert event_types.count("customer.vehicle_party.unlinked") == 1


def test_summary_is_null_without_a_catalogue_match_and_resolved_with_one(db_session):
    """KAN-31: VehiclePartySummary.make/model/trim/modelYear are nullable
    because vehicle_mdm only carries them through an OPTIONAL
    catalogue_variant — unlike the legacy Vehicle table, which had them as
    flat non-nullable columns. Both states, not just the happy path.
    """

    unmatched = _vehicle(db_session, vin="ZAR94000007123457")
    matched_variant = _catalogue_variant(db_session)
    matched = create_vehicle_mdm(
        db_session, vin="ZAR94000007123458", catalogue_variant_id=matched_variant.id,
        first_registration_date=dt.date(2019, 3, 1),
    )
    alice = _customer(db_session, "Alice")

    allocate_vehicle_party(
        db_session, vehicle_id=unmatched.id, customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=matched.id, customer_id=alice.id, role=VehiclePartyRole.DRIVER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    parties = {p.vehicle_id: p for p in list_customer_vehicles(db_session, customer_id=alice.id)}
    unmatched_summary = VehiclePartySummary.model_validate(parties[unmatched.id].vehicle, from_attributes=True)
    matched_summary = VehiclePartySummary.model_validate(parties[matched.id].vehicle, from_attributes=True)

    assert unmatched_summary.make is None
    assert unmatched_summary.model is None
    assert unmatched_summary.trim is None
    assert unmatched_summary.model_year is None
    assert unmatched_summary.vehicle_number == unmatched.vehicle_number

    assert matched_summary.make == "Alfa Romeo"
    assert matched_summary.model == "Giulietta"
    assert matched_summary.trim == "1.4 TB Progression"
    assert matched_summary.model_year == 2019  # the vehicle's OWN registration year, not the variant's 2016-2020 range


# KAN-99 — holders are per dealer group (Anto's ruling, 2026-10-04; ADR-014,
# ADR-064): vehicle_mdm is global, so two groups can each hold the same role
# on one VIN. An allocation closes only the incumbent in the CALLER's group,
# and the close and the insert are one transaction.

def _group_customer(db_session, group_id: uuid.UUID, first_name: str) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Muster",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


@pytest.mark.parametrize("role", list(VehiclePartyRole))
@pytest.mark.parametrize("a_first", [True, False], ids=["a-then-b", "b-then-a"])
def test_allocation_never_closes_another_groups_holder(db_session, role, a_first):
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000099")
    alice = _group_customer(db_session, group_a, "Alice")
    bruno = _group_customer(db_session, group_b, "Bruno")

    order = [(alice, group_a), (bruno, group_b)] if a_first else [(bruno, group_b), (alice, group_a)]
    parties = {
        group_id: allocate_vehicle_party(
            db_session, vehicle_id=vehicle.id, customer_id=customer.id, role=role,
            group_id=group_id, actor_id=uuid.uuid4(),
        )
        for customer, group_id in order
    }

    for party in parties.values():
        db_session.refresh(party)
        assert party.effective_to is None
    assert db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.vehicle_party.unlinked")
    ).all() == []


def test_same_group_close_is_stamped_with_that_group(db_session):
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000098")
    bruno = _group_customer(db_session, group_b, "Bruno")
    alice = _group_customer(db_session, group_a, "Alice")
    anna = _group_customer(db_session, group_a, "Anna")

    bruno_party = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=bruno.id, role=VehiclePartyRole.OWNER,
        group_id=group_b, actor_id=uuid.uuid4(),
    )
    alice_party = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.OWNER,
        group_id=group_a, actor_id=uuid.uuid4(),
    )
    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=anna.id, role=VehiclePartyRole.OWNER,
        group_id=group_a, actor_id=uuid.uuid4(),
    )

    db_session.refresh(alice_party)
    db_session.refresh(bruno_party)
    assert alice_party.effective_to is not None
    assert bruno_party.effective_to is None

    removals = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "vehicle_party_remove")).all()
    assert [(r.entity_id, r.tenant_id) for r in removals] == [(alice.id, group_a)]
    unlinked = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "customer.vehicle_party.unlinked")
    ).all()
    assert [(m.aggregate_id, m.tenant_id) for m in unlinked] == [(alice_party.id, group_a)]


def test_failure_after_the_close_leaves_the_previous_holder_open(db_session, monkeypatch):
    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000097")
    alice = _customer(db_session, "Alice")
    bob = _customer(db_session, "Bob")
    first = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=alice.id, role=VehiclePartyRole.KEEPER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    real_record = customer_service.record_audit_event

    def _fail_on_add(db, **kwargs):
        if kwargs["action"] == "vehicle_party_add":
            raise RuntimeError("forced failure after the close")
        return real_record(db, **kwargs)

    monkeypatch.setattr(customer_service, "record_audit_event", _fail_on_add)
    with pytest.raises(RuntimeError):
        allocate_vehicle_party(
            db_session, vehicle_id=vehicle.id, customer_id=bob.id, role=VehiclePartyRole.KEEPER,
            group_id=GROUP_ID, actor_id=uuid.uuid4(),
        )
    monkeypatch.undo()

    db_session.rollback()
    db_session.refresh(first)
    assert first.effective_to is None
    open_keepers = list_customer_vehicles(db_session, customer_id=bob.id)
    assert open_keepers == []
    assert db_session.scalars(select(AuditEvent).where(AuditEvent.action == "vehicle_party_remove")).all() == []


def _open_row(db_session, vehicle_id, customer, role, days_ago):
    row = VehicleParty(
        vehicle_id=vehicle_id, customer_id=customer.id, role=role,
        effective_from=utcnow() - dt.timedelta(days=days_ago), effective_to=None,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_allocation_closes_every_open_holder_in_the_callers_group(db_session):
    """A backdated create or a reopened row can leave two open holders of
    one (vehicle, role) in a group; a new holder closes all of them."""

    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000096")
    xaver, yara, zoe = (_customer(db_session, n) for n in ("Xaver", "Yara", "Zoe"))
    older = _open_row(db_session, vehicle.id, xaver, VehiclePartyRole.OWNER, days_ago=30)
    newer = _open_row(db_session, vehicle.id, yara, VehiclePartyRole.OWNER, days_ago=10)

    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=zoe.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    for row in (older, newer):
        db_session.refresh(row)
        assert row.effective_to is not None
    assert [p.customer_id for p in list_vehicle_parties(db_session, vehicle_id=vehicle.id, group_id=GROUP_ID)] == [zoe.id]


def test_reallocating_a_holder_who_shares_the_role_leaves_exactly_one_open(db_session):
    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000095")
    xaver, yara = _customer(db_session, "Xaver"), _customer(db_session, "Yara")
    _open_row(db_session, vehicle.id, xaver, VehiclePartyRole.OWNER, days_ago=30)
    _open_row(db_session, vehicle.id, yara, VehiclePartyRole.OWNER, days_ago=10)

    allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=xaver.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(),
    )

    assert [p.customer_id for p in list_vehicle_parties(db_session, vehicle_id=vehicle.id, group_id=GROUP_ID)] == [xaver.id]


def test_allocating_another_groups_customer_is_a_404_and_writes_nothing(db_session):
    group_b = uuid.uuid4()
    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000094")
    bruno = _group_customer(db_session, group_b, "Bruno")
    berta = _group_customer(db_session, group_b, "Berta")
    brunos = allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=bruno.id, role=VehiclePartyRole.OWNER,
        group_id=group_b, actor_id=uuid.uuid4(),
    )

    with pytest.raises(NotFoundError):
        allocate_vehicle_party(
            db_session, vehicle_id=vehicle.id, customer_id=berta.id, role=VehiclePartyRole.OWNER,
            group_id=GROUP_ID, actor_id=uuid.uuid4(),
        )
    db_session.rollback()

    assert [p.id for p in list_vehicle_parties(db_session, vehicle_id=vehicle.id, group_id=group_b)] == [brunos.id]
    assert list_customer_vehicles(db_session, customer_id=berta.id, include_closed=True) == []


@pytest.mark.parametrize("with_incumbent", [True, False], ids=["incumbent", "first-allocation"])
def test_concurrent_allocations_leave_exactly_one_open_holder(engine, db_session, monkeypatch, with_incumbent):
    """Two allocations of one (vehicle, role) in one group at the same
    time: the second waits for the first's commit, then closes its row.
    A row lock alone cannot do this — the second request never sees the
    row the first is inserting."""

    if engine.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only; the SQLite lane serialises writers itself")

    vehicle = _vehicle(db_session, vin="WVWZZZ1KZAW000093")
    xaver, yara, zoe = (_customer(db_session, n) for n in ("Xaver", "Yara", "Zoe"))
    db_session.commit()
    if with_incumbent:
        allocate_vehicle_party(
            db_session, vehicle_id=vehicle.id, customer_id=xaver.id, role=VehiclePartyRole.OWNER,
            group_id=GROUP_ID, actor_id=uuid.uuid4(),
        )

    first_inserted, release = threading.Event(), threading.Event()
    real_record = customer_service.record_audit_event

    def _pause_first(db, **kwargs):
        if kwargs["action"] == "vehicle_party_add" and threading.current_thread().name == "first":
            first_inserted.set()
            release.wait(timeout=10)
        return real_record(db, **kwargs)

    monkeypatch.setattr(customer_service, "record_audit_event", _pause_first)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _allocate(customer_id):
        with factory() as session:
            try:
                allocate_vehicle_party(
                    session, vehicle_id=vehicle.id, customer_id=customer_id, role=VehiclePartyRole.OWNER,
                    group_id=GROUP_ID, actor_id=uuid.uuid4(),
                )
            except Exception as exc:  # noqa: BLE001 — collected and asserted empty below, never swallowed
                errors.append(exc)

    first = threading.Thread(target=_allocate, args=(yara.id,), name="first")
    second = threading.Thread(target=_allocate, args=(zoe.id,), name="second")
    first.start()
    try:
        assert first_inserted.wait(timeout=10)
        second.start()
        second.join(timeout=1)
        assert second.is_alive()  # blocked behind the first allocation's lock
    finally:
        # Always let the paused first allocation finish, so a failed
        # assertion above is reported as itself, not as a teardown error.
        release.set()
        first.join(timeout=10)
        if second.ident is not None:  # started
            second.join(timeout=10)
        monkeypatch.undo()

    assert errors == []
    db_session.expire_all()
    open_holders = list_vehicle_parties(db_session, vehicle_id=vehicle.id, group_id=GROUP_ID)
    assert [p.customer_id for p in open_holders] == [zoe.id]
