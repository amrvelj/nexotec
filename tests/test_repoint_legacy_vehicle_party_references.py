"""KAN-31: the defensive repoint pass for any VehicleParty row still
pointing at a legacy vehicle.id. Zero such rows exist in any known
environment (Gap Analysis G-35) — this proves the script is correct on
the small population it exists to protect against, not against live
data.
"""

import uuid

from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition, VehicleStatus
from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm
from scripts.repoint_legacy_vehicle_party_references import run_repoint

GROUP_ID = uuid.uuid4()


def _customer(db_session) -> Customer:
    customer = Customer(
        group_id=GROUP_ID, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def _legacy_vehicle(db_session, vin="ZAR94000007123456") -> LegacyVehicle:
    vehicle = LegacyVehicle(
        vin=vin, make="Alfa Romeo", model="Giulietta", model_year=2020,
        condition=VehicleCondition.USED, status=VehicleStatus.IN_STOCK,
    )
    db_session.add(vehicle)
    db_session.flush()
    return vehicle


def _party(db_session, *, vehicle_id, customer_id) -> VehicleParty:
    party = VehicleParty(vehicle_id=vehicle_id, customer_id=customer_id, role=VehiclePartyRole.OWNER)
    db_session.add(party)
    db_session.flush()
    return party


def test_dry_run_writes_nothing(db_session):
    legacy = _legacy_vehicle(db_session)
    mdm, _created = create_or_get_vehicle_mdm(db_session, vin=legacy.vin, catalogue_variant_id=None)
    mdm.migrated_from_legacy_vehicle_id = legacy.id
    db_session.flush()
    customer = _customer(db_session)
    party = _party(db_session, vehicle_id=legacy.id, customer_id=customer.id)
    db_session.commit()

    report = run_repoint(db_session, commit=False)

    assert report.committed is False
    assert [o.outcome for o in report.outcomes] == ["repointed"]
    db_session.expire_all()
    refreshed = db_session.get(VehicleParty, party.id)
    assert refreshed.vehicle_id == legacy.id  # unchanged


def test_commit_repoints_a_stale_legacy_reference(db_session):
    legacy = _legacy_vehicle(db_session)
    mdm, _created = create_or_get_vehicle_mdm(db_session, vin=legacy.vin, catalogue_variant_id=None)
    mdm.migrated_from_legacy_vehicle_id = legacy.id
    db_session.flush()
    customer = _customer(db_session)
    party = _party(db_session, vehicle_id=legacy.id, customer_id=customer.id)
    db_session.commit()

    report = run_repoint(db_session, commit=True)

    assert report.committed is True
    outcome = report.outcomes[0]
    assert outcome.outcome == "repointed"
    assert outcome.new_vehicle_id == mdm.id
    db_session.expire_all()
    refreshed = db_session.get(VehicleParty, party.id)
    assert refreshed.vehicle_id == mdm.id


def test_rerunning_after_commit_is_a_no_op(db_session):
    legacy = _legacy_vehicle(db_session)
    mdm, _created = create_or_get_vehicle_mdm(db_session, vin=legacy.vin, catalogue_variant_id=None)
    mdm.migrated_from_legacy_vehicle_id = legacy.id
    db_session.flush()
    customer = _customer(db_session)
    _party(db_session, vehicle_id=legacy.id, customer_id=customer.id)
    db_session.commit()

    run_repoint(db_session, commit=True)
    second = run_repoint(db_session, commit=True)

    assert [o.outcome for o in second.outcomes] == ["already_mdm"]


def test_a_reference_with_no_known_replacement_is_reported_never_dropped(db_session):
    customer = _customer(db_session)
    orphan_id = uuid.uuid4()  # matches neither vehicle_mdm nor any migrated legacy id
    party = _party(db_session, vehicle_id=orphan_id, customer_id=customer.id)
    db_session.commit()

    report = run_repoint(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["unresolved"]
    db_session.expire_all()
    refreshed = db_session.get(VehicleParty, party.id)
    assert refreshed is not None  # never dropped
    assert refreshed.vehicle_id == orphan_id  # never guessed at


def test_a_row_already_pointing_at_vehicle_mdm_is_left_alone(db_session):
    mdm, _created = create_or_get_vehicle_mdm(db_session, vin="ZAR94000007123457", catalogue_variant_id=None)
    customer = _customer(db_session)
    party = _party(db_session, vehicle_id=mdm.id, customer_id=customer.id)
    db_session.commit()

    report = run_repoint(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["already_mdm"]
    db_session.expire_all()
    refreshed = db_session.get(VehicleParty, party.id)
    assert refreshed.vehicle_id == mdm.id
