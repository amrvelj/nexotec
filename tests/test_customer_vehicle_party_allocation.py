"""WP-5 PR-9, ADR-064: setting a new holder for a role CLOSES the previous
one rather than overwriting it — never an update, never a silent
overwrite, never a delete.
"""

import uuid

from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehiclePartyRole
from app.customer.services.customer import allocate_vehicle_party, delete_customer_vehicle, list_customer_vehicles
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
