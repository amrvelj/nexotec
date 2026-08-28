"""WP-5 PR-7: reconciliation additions — new cross-context checks, and the
unrepointed-legacy-reference count that gates old-table retirement.
"""

import uuid

from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition
from app.vehicle.models.vehicle import VehicleStatus as LegacyVehicleStatus
from app.vehicle.reconciliation import count_unrepointed_legacy_vehicle_party_references
from scripts.migrate_legacy_vehicles import run_migration


def test_no_unrepointed_references_when_nothing_migrated(db_session):
    assert count_unrepointed_legacy_vehicle_party_references(db_session) == 0


def test_counts_a_vehicle_party_still_pointing_at_a_migrated_legacy_id(db_session):
    legacy = LegacyVehicle(
        vin="ZAR94000007123456", make="Alfa Romeo", model="Giulietta", model_year=2016,
        condition=VehicleCondition.USED, status=LegacyVehicleStatus.IN_STOCK,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    run_migration(db_session, commit=True)

    db_session.add(
        VehicleParty(vehicle_id=legacy.id, customer_id=uuid.uuid4(), role=VehiclePartyRole.OWNER)
    )
    db_session.commit()

    assert count_unrepointed_legacy_vehicle_party_references(db_session) == 1
