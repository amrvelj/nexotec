"""KAN-564 (ADR-121; Anto, 2026-11-07, option B, decisions 1 A and 2 A):
when a configuration is deleted for retention, the personal values its own
earlier audit rows copied are blanked there too.

Before, `update_configuration` wrote the old and new VIN, plate, mileage and
notes into the append-only audit log, and the 30-day purge (KAN-270) deleted
the configuration but left those rows: the personal data outlived the
configuration with no lifetime (revDSG). Now the purge, and the discard of a
host's failed copy (KAN-188), blank those keys in the configuration's own
rows through `app.core.audit.redact_audit_values`, the log's one sanctioned
rewrite: no row is deleted, the key stays («VIN geändert» is still readable),
every other column is untouched, and one `redact` row records which rows and
keys, never the values. A configuration a host took is never deleted, so its
history stays whole (FR-C-11). On Postgres.
"""

import uuid

from sqlalchemy import select

from app.core.audit import REDACTED, record_audit_event, redact_audit_values
from app.core.audit_model import AuditEvent
from app.vehicle.daily_jobs import run_daily_unattached_configuration_purge
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.schemas.configuration import ConfigurationUpdate
from app.vehicle.services import configuration as configuration_service
from tests.test_configurator_hosts import _dealership, _record_configuration
from tests.test_vehicle_configuration_retention import _age, _held_by_stock_item

OLD_VIN = "WVWZZZ1KZAW564001"
NEW_VIN = "WVWZZZ1KZAW564002"


def _rows(db, configuration_id: uuid.UUID) -> list[AuditEvent]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "vehicle_configuration", AuditEvent.entity_id == configuration_id)
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )


def _edited(db, tenant_id) -> uuid.UUID:
    """A record-mode configuration whose VIN, plate, mileage and notes were
    changed once — the update row holds old and new values of each."""

    config = _record_configuration(db, tenant_id, vin=OLD_VIN)
    configuration_service.update_configuration(
        db, configuration=config, actor_id=uuid.uuid4(),
        data=ConfigurationUpdate(vin=NEW_VIN, licence_plate="ZH999999", mileage_km=150000, notes="Kunde Herr Muster"),
    )
    db.commit()
    return config.id


def _update_row(rows: list[AuditEvent]) -> AuditEvent:
    return next(row for row in rows if row.action == "update")


def test_the_edited_configurations_update_row_holds_the_personal_values_before_any_purge(db_session):
    dealership = _dealership(db_session)
    configuration_id = _edited(db_session, dealership.id)

    row = _update_row(_rows(db_session, configuration_id))

    assert row.before["vin"] == OLD_VIN and row.after["vin"] == NEW_VIN
    assert row.after["licencePlate"] == "ZH999999"
    assert row.after["mileageKm"] == 150000
    assert row.after["notes"] == "Kunde Herr Muster"


def test_the_purge_blanks_the_personal_values_in_the_configurations_own_rows(db_session):
    dealership = _dealership(db_session)
    configuration_id = _edited(db_session, dealership.id)
    before = {row.id: (row.action, row.actor_id, row.reason, row.created_at) for row in _rows(db_session, configuration_id)}
    _age(db_session, configuration_id, days=31)

    run_daily_unattached_configuration_purge(db_session)

    assert db_session.get(VehicleConfiguration, configuration_id) is None
    rows = _rows(db_session, configuration_id)
    # No row deleted, none re-dated, no other column touched.
    assert {row.id for row in rows} >= set(before)
    for row in rows:
        if row.id in before:
            assert (row.action, row.actor_id, row.reason, row.created_at) == before[row.id]
    update_row = _update_row(rows)
    # The key stays: the row still says which field changed.
    for key in ("vin", "licencePlate", "mileageKm", "notes"):
        assert update_row.after[key] == REDACTED, key
    assert update_row.before["vin"] == REDACTED
    # Nothing personal anywhere in the configuration's rows.
    text = repr([(row.before, row.after) for row in rows])
    for value in (OLD_VIN, NEW_VIN, "ZH999999", "ZH123456", "150000", "148000", "Kunde Herr Muster"):
        assert value not in text, value


def test_the_purge_records_one_redact_row_naming_rows_and_keys_never_values(db_session):
    dealership = _dealership(db_session)
    configuration_id = _edited(db_session, dealership.id)
    update_id = _update_row(_rows(db_session, configuration_id)).id
    _age(db_session, configuration_id, days=31)

    run_daily_unattached_configuration_purge(db_session)

    redacts = [row for row in _rows(db_session, configuration_id) if row.action == "redact"]
    assert len(redacts) == 1
    assert redacts[0].reason == "never_taken_purged"
    assert str(update_id) in redacts[0].after["auditEventIds"]
    assert redacts[0].after["keys"] == sorted(configuration_service.PERSONAL_AUDIT_KEYS)
    assert redacts[0].before is None


def test_a_configuration_a_host_took_keeps_its_full_history(db_session):
    dealership = _dealership(db_session)
    configuration_id = _held_by_stock_item(db_session, dealership)
    config = db_session.get(VehicleConfiguration, configuration_id)
    configuration_service.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(), data=ConfigurationUpdate(vin=NEW_VIN),
    )
    db_session.commit()
    _age(db_session, configuration_id, days=31)

    run_daily_unattached_configuration_purge(db_session)

    assert db_session.get(VehicleConfiguration, configuration_id) is not None
    rows = _rows(db_session, configuration_id)
    assert _update_row(rows).after["vin"] == NEW_VIN
    assert not [row for row in rows if row.action == "redact"]


def test_the_discard_of_a_hosts_failed_copy_redacts_too(db_session):
    """Decision 2 A: one rule for every deletion of a configuration."""

    dealership = _dealership(db_session)
    configuration_id = _edited(db_session, dealership.id)
    config = db_session.get(VehicleConfiguration, configuration_id)

    configuration_service.discard_configuration_copy(db_session, configuration=config, actor_id=uuid.uuid4())

    rows = _rows(db_session, configuration_id)
    assert _update_row(rows).after["vin"] == REDACTED
    redacts = [row for row in rows if row.action == "redact"]
    assert len(redacts) == 1 and redacts[0].reason == "host_copy_compensated"


def test_a_redaction_never_touches_another_entitys_rows(db_session):
    dealership = _dealership(db_session)
    other_id = uuid.uuid4()
    record_audit_event(
        db_session, entity_type="vehicle_configuration", entity_id=other_id, tenant_id=dealership.id,
        action="update", actor_id=None, before={"vin": OLD_VIN}, after={"vin": NEW_VIN},
    )
    record_audit_event(
        db_session, entity_type="vehicle_mdm", entity_id=uuid.uuid4(), tenant_id=None,
        action="update", actor_id=None, before={"vin": OLD_VIN}, after={"vin": NEW_VIN},
    )
    configuration_id = _edited(db_session, dealership.id)
    _age(db_session, configuration_id, days=31)

    run_daily_unattached_configuration_purge(db_session)

    untouched = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id != configuration_id)).all()
    assert all(row.after.get("vin") == NEW_VIN for row in untouched if row.after and "vin" in row.after)


def test_redaction_leaves_null_values_null_and_other_keys_alone(db_session):
    entity_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    record_audit_event(
        db_session, entity_type="vehicle_configuration", entity_id=entity_id, tenant_id=tenant_id,
        action="update", actor_id=None, before={"vin": None, "ps": 92}, after={"vin": NEW_VIN, "ps": 110},
    )

    touched = redact_audit_values(
        db_session, entity_type="vehicle_configuration", entity_id=entity_id, tenant_id=tenant_id,
        keys=frozenset({"vin"}), actor_id=None, reason="test",
    )
    db_session.commit()

    assert touched == 1
    row = next(r for r in _rows(db_session, entity_id) if r.action == "update")
    assert row.before == {"vin": None, "ps": 92}
    assert row.after == {"vin": REDACTED, "ps": 110}


def test_a_young_configuration_is_not_purged_and_keeps_its_values(db_session):
    dealership = _dealership(db_session)
    configuration_id = _edited(db_session, dealership.id)
    _age(db_session, configuration_id, days=3)

    run_daily_unattached_configuration_purge(db_session)

    rows = _rows(db_session, configuration_id)
    assert _update_row(rows).after["vin"] == NEW_VIN
    assert not [row for row in rows if row.action == "redact"]
