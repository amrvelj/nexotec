"""WP-5 PR-9, ADR-064: setting a new holder for a role CLOSES the previous
one rather than overwriting it — never an update, never a silent
overwrite, never a delete.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehiclePartyRole
from app.customer.schemas.customer import VehiclePartySummary
from app.customer.services.customer import allocate_vehicle_party, delete_customer_vehicle, list_customer_vehicles
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
