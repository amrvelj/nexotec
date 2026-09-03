"""WP-5 PR-7: the one-way legacy-vehicle migration (ADR-021). Dry run
writes nothing; re-running never migrates twice; a VIN already in
vehicle-mdm ATTACHES (FR-V-04), never duplicates; a legacy row with no
valid VIN is `rejected`, never given an invented one (ADR-045).
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.vehicle.models.vehicle import CustodyEventType as LegacyCustodyEventType
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition
from app.vehicle.models.vehicle import VehicleCustodyEvent as LegacyCustodyEvent
from app.vehicle.models.vehicle import VehicleStatus as LegacyVehicleStatus
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as NewCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm
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
    new_events = list(db_session.scalars(select(NewCustodyEvent).where(NewCustodyEvent.vehicle_id == migrated.id)).all())
    assert len(new_events) == 1
    assert new_events[0].partner_id == partner_id


# --- FR-V-04: a VIN already in vehicle-mdm attaches, never duplicates ----------


def test_vin_already_in_vehicle_mdm_attaches_not_duplicates(db_session):
    existing, created = create_or_get_vehicle_mdm(
        db_session, vin="ZAR94000007123456", catalogue_variant_id=None
    )
    assert created is True
    db_session.commit()

    legacy = _legacy_vehicle(db_session, vin="ZAR94000007123456")

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["matched"]
    all_mdm = list(db_session.scalars(select(VehicleMdm)).all())
    assert len(all_mdm) == 1
    db_session.refresh(existing)
    assert existing.migrated_from_legacy_vehicle_id == legacy.id
    # The attached row keeps its own identity — the migration never
    # allocates it a second vehicle_number.
    assert existing.vehicle_number == all_mdm[0].vehicle_number


def test_attach_carries_custody_history_onto_the_existing_row(db_session):
    existing, _ = create_or_get_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    db_session.commit()
    legacy = _legacy_vehicle(db_session, vin="ZAR94000007123456")
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
    carried = list(
        db_session.scalars(select(NewCustodyEvent).where(NewCustodyEvent.vehicle_id == existing.id)).all()
    )
    assert len(carried) == 1 and carried[0].partner_id == partner_id


def test_rerunning_after_an_attach_is_a_noop(db_session):
    create_or_get_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    db_session.commit()
    _legacy_vehicle(db_session, vin="ZAR94000007123456")
    run_migration(db_session, commit=True)

    second = run_migration(db_session, commit=True)

    assert [o.outcome for o in second.outcomes] == ["already_migrated"]
    assert len(list(db_session.scalars(select(VehicleMdm)).all())) == 1


def test_dry_run_would_attach_writes_nothing(db_session):
    existing, _ = create_or_get_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    db_session.commit()
    _legacy_vehicle(db_session, vin="ZAR94000007123456")

    report = run_migration(db_session, commit=False)

    assert [o.outcome for o in report.outcomes] == ["would_attach"]
    db_session.refresh(existing)
    assert existing.migrated_from_legacy_vehicle_id is None


# --- ADR-045: a legacy row with no valid VIN cannot enter vehicle-mdm ----------


def test_malformed_vin_is_rejected_not_given_an_invented_one(db_session):
    _legacy_vehicle(db_session, vin="not-a-valid-vin")  # lowercase, hyphen, wrong length

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["rejected"]
    assert "VIN mandatory" in report.outcomes[0].notes
    assert db_session.scalar(select(VehicleMdm)) is None


# --- ambiguous: reported, never guessed ---------------------------------------


def test_vin_already_attached_to_a_different_legacy_id_is_ambiguous(db_session):
    other_legacy_id = uuid.uuid4()
    existing, _ = create_or_get_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)
    existing.migrated_from_legacy_vehicle_id = other_legacy_id
    db_session.commit()

    _legacy_vehicle(db_session, vin="ZAR94000007123456")

    report = run_migration(db_session, commit=True)

    assert [o.outcome for o in report.outcomes] == ["ambiguous"]
    assert str(other_legacy_id) in report.outcomes[0].notes


def test_dry_run_on_an_empty_database_is_readable(db_session):
    report = run_migration(db_session, commit=False)

    assert report.total_legacy_rows == 0
    assert report.outcomes == []
    assert "DRY RUN" in report.summary()
    assert "legacy rows examined: 0" in report.summary()
