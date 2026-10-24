"""KAN-395 (Anto, 2026-10-20, option B: clean the data once): a configuration
whose VIN was stored before KAN-338 as no VIN — an O for a 0, 16 characters,
lower case — could no longer be saved, because every PATCH judges the stored
VIN (KAN-353) and the user cannot correct it on screen (KAN-340).
`repair_stored_vins` (scripts/repair_configuration_vins.py) normalises what
normalises to a VIN, clears what cannot be one, leaves a pre-1981 car alone
(KAN-391's question) and audits every change with the old value.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.services.configuration import VIN_REPAIR_REASON, repair_stored_vins
from scripts.repair_configuration_vins import run
from tests.configuration_test_support import seed_manual_coded_lists
from tests.test_vin_normalised_on_every_input import (
    VIN,
    _headers,
    _known_car,
    _post_valuation_on,
    _record_configuration,
)

TYPO = "WVWZZZ1JZ3W38625O"  # an O for the final 0 — the ticket's reproduction


def _stored_as_before_kan_338(db_session, configuration: dict, vin: str, registered: dt.date | None) -> None:
    """The VIN the field held before KAN-338: whatever was typed, unchecked."""

    row = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    row.vin = vin
    row.first_registration_date = registered
    db_session.commit()


def _save_mileage(client, tenant_id: uuid.UUID, configuration_id: str):
    current = client.get(f"/v1/configurations/{configuration_id}", headers=_headers(tenant_id)).json()
    return client.patch(
        f"/v1/configurations/{configuration_id}",
        json={"mileageKm": 1000},
        headers=_headers(tenant_id, **{"If-Match": str(current["version"])}),
    )


def test_a_stored_non_vin_blocks_every_save_until_the_repair_clears_it(client, db_session):
    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    configuration = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, configuration, TYPO, dt.date(2015, 3, 1))

    refused = _save_mileage(client, tenant_id, configuration["id"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == "chassis_number_requires_pre_1981_registration"

    repairs = run(db_session, commit=True)

    assert [(r.configuration_id, r.before, r.after) for r in repairs] == [(uuid.UUID(configuration["id"]), TYPO, None)]
    saved = _save_mileage(client, tenant_id, configuration["id"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["vin"] is None and saved.json()["mileageKm"] == 1000
    audit = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_id == uuid.UUID(configuration["id"]), AuditEvent.action == "vin_repair"
        )
    ).one()
    assert (audit.before["vin"], audit.after["vin"], audit.actor_id, audit.reason) == (
        TYPO,
        None,
        None,
        VIN_REPAIR_REASON,
    )


def test_a_vin_that_normalises_is_kept_normalised_and_links_its_car(client, db_session):
    seed_manual_coded_lists(db_session)
    vehicle = _known_car(db_session)
    tenant_id = uuid.uuid4()
    configuration = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, configuration, VIN.lower(), None)
    assert _save_mileage(client, tenant_id, configuration["id"]).status_code == 422

    run(db_session, commit=True)

    db_session.expire_all()
    row = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    assert (row.vin, row.vehicle_id, row.updated_by) == (VIN, vehicle.id, None)
    assert row.version == configuration["version"] + 1
    assert _save_mileage(client, tenant_id, configuration["id"]).status_code == 200


def test_a_valuation_created_from_a_repaired_configuration_is_accepted(client, db_session):
    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    configuration = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, configuration, "WVWZZZ1JZ3W38625", dt.date(2003, 1, 1))  # 16 characters
    assert _post_valuation_on(client, tenant_id, configuration["id"]).status_code == 422

    run(db_session, commit=True)

    accepted = _post_valuation_on(client, tenant_id, configuration["id"])
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["vehicleVin"] is None


def test_the_repair_leaves_valid_vins_empty_ones_and_pre_1981_cars_alone(client, db_session):
    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    valid = _record_configuration(client, tenant_id, vin=VIN)
    empty = _record_configuration(client, tenant_id)
    oldtimer = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, oldtimer, "911 230-0123", dt.date(1972, 5, 1))
    events_before = db_session.query(OutboxMessage).count()

    assert run(db_session, commit=True) == []

    db_session.expire_all()
    assert db_session.get(VehicleConfiguration, uuid.UUID(valid["id"])).vin == VIN
    assert db_session.get(VehicleConfiguration, uuid.UUID(empty["id"])).vin is None
    assert db_session.get(VehicleConfiguration, uuid.UUID(oldtimer["id"])).vin == "911 230-0123"
    assert db_session.query(OutboxMessage).count() == events_before
    assert _save_mileage(client, tenant_id, oldtimer["id"]).status_code == 200


def test_the_dry_run_writes_nothing_and_a_second_run_finds_nothing(client, db_session):
    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    configuration = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, configuration, TYPO, None)

    assert len(run(db_session, commit=False)) == 1
    db_session.expire_all()
    row = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    assert (row.vin, row.version) == (TYPO, configuration["version"])
    assert not db_session.scalars(select(AuditEvent).where(AuditEvent.action == "vin_repair")).all()

    assert len(run(db_session, commit=True)) == 1
    assert run(db_session, commit=True) == []


def test_a_repair_publishes_configuration_updated(client, db_session):
    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    configuration = _record_configuration(client, tenant_id)
    _stored_as_before_kan_338(db_session, configuration, TYPO, None)

    repair_stored_vins(db_session)
    db_session.commit()

    published = db_session.scalars(
        select(OutboxMessage.event_type).where(OutboxMessage.aggregate_id == uuid.UUID(configuration["id"]))
    ).all()
    assert published.count("configuration.updated") == 1
