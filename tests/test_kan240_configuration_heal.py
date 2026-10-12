"""KAN-240 (Anto's ruling, 2026-10-12): resolving a mapping gap fills the NULL
spec-block and coded fields of the provider configurations built from the
variants it healed — never a set value, never a field the advisor cleared,
never another variant's configurations — in the resolve's own transaction,
with a version bump, a system audit record and `configuration.updated`.
"""

import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.integration.models.provider import IntegrationProvider
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMode,
    ConfigurationSource,
    VehicleConfiguration,
)
from app.vehicle.schemas.configuration import ConfigurationCreate
from app.vehicle.services import catalogue_admin
from app.vehicle.services import configuration as configuration_service
from tests.test_vehicle_catalogue_sync import (
    _fuel_gap,
    _make_connection,
    _seed_reference_list,
    _seeded_tenant,
    _variant,
)


def _configuration(db_session, *, tenant_id: uuid.UUID, fz_key: str) -> VehicleConfiguration:
    return configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE,
            catalogue_variant_id=_variant(db_session, fz_key).id,
        ),
    )


def _reload(db_session, configuration: VehicleConfiguration) -> VehicleConfiguration:
    db_session.expire_all()
    reloaded = db_session.get(VehicleConfiguration, configuration.id)
    assert reloaded is not None
    return reloaded


def _resolve_fuel_3_as_petrol(db_session) -> None:
    catalogue_admin.resolve_gap(
        db_session, gap=_fuel_gap(db_session, "3"), canonical_list_code="fuel_type", canonical_value_code="petrol",
        actor_id=uuid.uuid4(),
    )


def _fills(db_session, configuration: VehicleConfiguration) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == configuration.id, AuditEvent.action == "catalogue_fill")
        )
    )


def _setup(db_session) -> uuid.UUID:
    tenant_id = _seeded_tenant(db_session)  # mock catalogue, empty code map: every coded field NULL
    _seed_reference_list(db_session, "fuel_type", ("petrol", "diesel"))
    return tenant_id


def test_a_configuration_with_a_null_fuel_type_is_filled_when_the_gap_is_resolved(db_session):
    tenant_id = _setup(db_session)
    config = _configuration(db_session, tenant_id=tenant_id, fz_key="FZ100001")
    assert config.fuel_type is None
    version = config.version

    _resolve_fuel_3_as_petrol(db_session)  # no sync, no configuration call after it

    filled = _reload(db_session, config)
    assert filled.fuel_type == "petrol"
    assert filled.version == version + 1
    (audit,) = _fills(db_session, filled)
    assert audit.actor_id is None  # the system, not the admin who resolved the gap
    assert audit.tenant_id == tenant_id
    assert audit.before == {"fuel_type": None}
    assert audit.after == {"fuel_type": "petrol"}
    assert "resolved" in (audit.reason or "")
    events = db_session.scalars(
        select(OutboxMessage.event_type).where(
            OutboxMessage.aggregate_id == config.id, OutboxMessage.event_type == "configuration.updated"
        )
    ).all()
    assert len(events) == 1
    gap_audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "vehicle_mapping_gap", AuditEvent.action == "resolve")
    ).one()
    assert gap_audit.after["configurationsFilled"] == 1


def test_a_configuration_holding_a_value_keeps_it(db_session):
    tenant_id = _setup(db_session)
    config = _configuration(db_session, tenant_id=tenant_id, fz_key="FZ100001")
    row = _reload(db_session, config)
    row.fuel_type = "diesel"  # the advisor's value
    db_session.commit()
    version = row.version

    _resolve_fuel_3_as_petrol(db_session)

    kept = _reload(db_session, config)
    assert (kept.fuel_type, kept.version) == ("diesel", version)
    assert _fills(db_session, kept) == []


def test_a_field_the_advisor_cleared_stays_null(db_session):
    tenant_id = _setup(db_session)
    config = _configuration(db_session, tenant_id=tenant_id, fz_key="FZ100001")
    row = _reload(db_session, config)
    row.overridden_fields = ["fuel_type"]
    db_session.commit()

    _resolve_fuel_3_as_petrol(db_session)

    assert _reload(db_session, config).fuel_type is None
    assert _fills(db_session, config) == []


def test_each_tenant_s_configurations_follow_only_their_own_variant(db_session):
    """The variant is global (ADR-075); each configuration is its tenant's.
    Tenant B's configuration of a variant with the gap's raw code is filled
    and audited in tenant B; one of a variant with another raw code is not."""

    tenant_a = _setup(db_session)
    tenant_b = uuid.uuid4()
    _make_connection(db_session, db_session.scalars(select(IntegrationProvider)).one(), tenant_id=tenant_b)
    config_a = _configuration(db_session, tenant_id=tenant_a, fz_key="FZ100001")
    config_b = _configuration(db_session, tenant_id=tenant_b, fz_key="FZ100002")
    other_variant_b = _configuration(db_session, tenant_id=tenant_b, fz_key="FZ100003")  # raw fuel code "4"

    _resolve_fuel_3_as_petrol(db_session)

    assert _reload(db_session, config_a).fuel_type == "petrol"
    assert _reload(db_session, config_b).fuel_type == "petrol"
    (audit_b,) = _fills(db_session, config_b)
    assert audit_b.tenant_id == tenant_b
    untouched = _reload(db_session, other_variant_b)
    assert (untouched.fuel_type, untouched.version) == (None, 1)


def test_a_manual_configuration_pointing_at_the_variant_is_not_touched(db_session):
    tenant_id = _setup(db_session)
    manual = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL,
        ),
    )
    row = _reload(db_session, manual)
    row.catalogue_variant_id = _variant(db_session, "FZ100001").id
    db_session.commit()

    _resolve_fuel_3_as_petrol(db_session)

    assert _reload(db_session, manual).fuel_type is None
