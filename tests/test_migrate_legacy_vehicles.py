"""WP-5 PR-7: the one-way legacy-vehicle migration. Dry run writes nothing;
re-running after a commit reports every row as already_migrated, never
migrates twice.
"""

import uuid

from sqlalchemy import select

from app.vehicle.models.vehicle import CustodyEventType as LegacyCustodyEventType
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition
from app.vehicle.models.vehicle import VehicleCustodyEvent as LegacyCustodyEvent
from app.vehicle.models.vehicle import VehicleStatus as LegacyVehicleStatus
from app.vehicle.models.vehicle_mdm import VehicleMdm
from scripts.migrate_legacy_vehicles import run_migration


def _legacy_vehicle(db_session, **overrides) -> LegacyVehicle:
    defaults = {
        "vin": "ZAR94000007123456", "make": "Alfa Romeo", "model": "Giulietta", "model_year": 2016,
        "condition": VehicleCondition.USED, "status": LegacyVehicleStatus.IN_STOCK,
    }
    defaults.update(overrides)
    vehicle = LegacyVehicle(**defaults)
    db_session.add(vehicle)
    db_session.commit()
    db_session.refresh(vehicle)
    return vehicle


def test_dry_run_writes_nothing(db_session):
    _legacy_vehicle(db_session)

    report = run_migration(db_session, commit=False)

    assert report.committed is False
    assert report.total_legacy_rows == 1
    assert report.outcomes[0].outcome == "would_create"
    assert db_session.scalar(select(VehicleMdm)) is None


def test_commit_creates_vehicle_mdm_row(db_session):
    legacy = _legacy_vehicle(db_session)

    report = run_migration(db_session, commit=True)

    assert report.committed is True
    assert report.outcomes[0].outcome == "created"
    migrated = db_session.scalar(select(VehicleMdm).where(VehicleMdm.migrated_from_legacy_vehicle_id == legacy.id))
    assert migrated is not None
    assert migrated.vin == legacy.vin
    assert migrated.catalogue_match_status.value == "unverified"


def test_rerunning_after_commit_reports_already_migrated_not_a_duplicate(db_session):
    _legacy_vehicle(db_session)
    run_migration(db_session, commit=True)

    second_report = run_migration(db_session, commit=True)

    assert all(o.outcome == "already_migrated" for o in second_report.outcomes)
    all_mdm_rows = list(db_session.scalars(select(VehicleMdm)).all())
    assert len(all_mdm_rows) == 1


def test_totaled_status_is_approximated_and_flagged(db_session):
    _legacy_vehicle(db_session, status=LegacyVehicleStatus.TOTALED)

    report = run_migration(db_session, commit=True)

    assert report.outcomes[0].notes  # flagged, not silent
    migrated = db_session.scalar(select(VehicleMdm))
    assert migrated.vehicle_status.value == "scrapped"


def test_odometer_without_custodian_is_skipped_not_guessed(db_session):
    _legacy_vehicle(db_session, odometer=50000, current_custodian_partner_id=None)

    report = run_migration(db_session, commit=True)

    skip_outcomes = [o for o in report.outcomes if o.outcome == "odometer_skipped_no_custodian"]
    assert len(skip_outcomes) == 1


def test_legacy_custody_events_carry_across_repointed(db_session):
    import datetime as dt

    legacy = _legacy_vehicle(db_session)
    partner_id = uuid.uuid4()
    db_session.add(
        LegacyCustodyEvent(
            vehicle_id=legacy.id, partner_id=partner_id, event_type=LegacyCustodyEventType.ACQUIRED,
            event_date=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        )
    )
    db_session.commit()

    report = run_migration(db_session, commit=True)

    assert report.custody_events_carried == 1
    migrated = db_session.scalar(select(VehicleMdm))
    from app.vehicle.models.vehicle_history import VehicleCustodyEvent as NewCustodyEvent

    new_events = list(db_session.scalars(select(NewCustodyEvent).where(NewCustodyEvent.vehicle_id == migrated.id)).all())
    assert len(new_events) == 1
    assert new_events[0].partner_id == partner_id
