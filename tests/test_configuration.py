"""Configurator C-C (KAN-41) — the configuration entity, service + API.

The "never writes vehicle_mdm" invariant is in
`tests/architecture/test_configuration_never_writes_vehicle_mdm.py`; the
spec-block-carrier drift is in `test_spec_block_carriers_do_not_drift.py`.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMatchStatus,
    ConfigurationMode,
    ConfigurationSource,
)
from app.vehicle.schemas.configuration import (
    ConfigurationCreate,
    ConfigurationOptionInput,
    ConfigurationUpdate,
)
from app.vehicle.schemas.spec_block import VehicleSpecBlockInput
from app.vehicle.services import configuration as svc


def _variant(db, *, name="Golf GTI", **spec) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db.add(brand)
    db.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db.add(group)
    db.flush()
    v = ModelVariant(
        model_group_id=group.id, name=name, model_year_from=2021, vehicle_kind="passenger_car",
        fuel_type="petrol", ps=245, base_price=Decimal("42500.00"), **spec,
    )
    db.add(v)
    db.flush()
    db.commit()
    return v


# --- service ------------------------------------------------------


def test_provider_create_copies_the_spec_block_and_is_matched(db_session):
    variant = _variant(db_session, displacement_ccm=1984)
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()

    config = svc.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
        ),
    )

    assert config.catalogue_match_status == ConfigurationMatchStatus.MATCHED
    assert config.catalogue_variant_id == variant.id
    assert config.ps == 245
    assert config.displacement_ccm == 1984
    assert config.brand_display_name == "Volkswagen"
    assert config.variant_name == "Golf GTI"
    assert config.catalogue_variant_label == "Volkswagen Golf Golf GTI"


def test_manual_create_is_unverified_with_hand_entered_spec(db_session):
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    config = svc.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL,
            spec=VehicleSpecBlockInput(ps=54, displacement_ccm=1300),
            brand_display_name="Citroën", variant_name="2CV6",
            first_registration_date=dt.date(1985, 4, 1), mileage_km=142000,
        ),
    )
    assert config.catalogue_match_status == ConfigurationMatchStatus.UNVERIFIED
    assert config.catalogue_variant_id is None
    assert config.ps == 54
    assert config.brand_display_name == "Citroën"
    assert config.mileage_km == 142000


def test_vin_hit_links_to_the_existing_vehicle_mdm(db_session):
    from app.vehicle.services.vehicle_mdm import create_or_get_vehicle_mdm

    vehicle, _ = create_or_get_vehicle_mdm(db_session, vin="WVWZZZ1KZAW111222", catalogue_variant_id=None)
    db_session.commit()
    vehicle_id, vehicle_number = vehicle.id, vehicle.vehicle_number

    config = svc.create_configuration(
        db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.VIN, vin="WVWZZZ1KZAW111222",
        ),
    )
    assert config.vehicle_id == vehicle_id
    assert config.vehicle_label == vehicle_number


def test_update_bumps_version_records_overridden_fields_and_audits(db_session):
    variant = _variant(db_session)
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    config = svc.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
        ),
    )
    v0 = config.version

    config = svc.update_configuration(
        db_session, configuration=config, actor_id=actor_id,
        data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=260)),
    )
    assert config.version == v0 + 1
    assert config.ps == 260
    assert "ps" in config.overridden_fields

    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "vehicle_configuration", AuditEvent.action == "update")
    ).all()
    assert len(audit) == 1
    assert audit[0].before == {"ps": 245} and audit[0].after == {"ps": 260}


def test_match_status_transition_audits_and_fires_configuration_matched(db_session):
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    config = svc.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL, spec=VehicleSpecBlockInput(ps=90),
        ),
    )
    svc.update_configuration(
        db_session, configuration=config, actor_id=actor_id,
        data=ConfigurationUpdate(catalogue_match_status=ConfigurationMatchStatus.BEST_MATCH_CONFIRMED),
    )
    events = db_session.scalars(
        select(OutboxMessage.event_type).where(OutboxMessage.aggregate_id == config.id)
    ).all()
    assert "configuration.created" in events
    assert "configuration.matched" in events
    assert db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "vehicle_configuration", AuditEvent.action == "match_status_change"
        )
    ).all()


def test_audit_survives_a_first_registration_date_in_the_payload(db_session):
    """KAN-29 regression — a dt.date reaching the audit payload must be
    isoformat'd, not passed raw to json.dumps."""

    config = svc.create_configuration(
        db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL,
            first_registration_date=dt.date(2015, 6, 1),
            spec=VehicleSpecBlockInput(),
        ),
    )
    row = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "vehicle_configuration", AuditEvent.entity_id == config.id)
    ).one()
    assert row.after["firstRegistrationDate"] == "2015-06-01"

    # And an update carrying the date through a spec-field change.
    svc.update_configuration(
        db_session, configuration=config, actor_id=uuid.uuid4(),
        data=ConfigurationUpdate(first_registration_date=dt.date(2016, 1, 1), spec=VehicleSpecBlockInput(ps=95)),
    )  # must not raise


def test_copy_makes_a_new_id_with_the_same_content(db_session):
    variant = _variant(db_session)
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    original = svc.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
        ),
    )
    svc.replace_options(
        db_session, configuration=original, actor_id=actor_id,
        options=[ConfigurationOptionInput(description="Metallic paint", price=Decimal("800.00"))],
    )
    original = svc.get_configuration_or_404(db_session, tenant_id=tenant_id, configuration_id=original.id)

    copy = svc.copy_configuration(db_session, source=original, actor_id=actor_id)
    assert copy.id != original.id
    assert copy.ps == original.ps
    assert [o.description for o in copy.options] == ["Metallic paint"]


def test_tenant_isolation_is_a_404(db_session):
    config = svc.create_configuration(
        db_session, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL, mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL, spec=VehicleSpecBlockInput(),
        ),
    )
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        svc.get_configuration_or_404(db_session, tenant_id=uuid.uuid4(), configuration_id=config.id)


# --- API --------------------------------------------------------


def _bearer(tenant_id: uuid.UUID, role: AccessRole = AccessRole.SALES) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({role}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


def test_api_round_trip_create_read_patch(client, db_session):
    variant = _variant(db_session)
    tenant_id = uuid.uuid4()
    headers = _bearer(tenant_id)

    created = client.post(
        "/v1/configurations",
        json={"source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
              "catalogueVariantId": str(variant.id)},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["catalogueMatchStatus"] == "matched"
    assert body["spec"]["ps"] == 245
    cid, version = body["id"], body["version"]

    got = client.get(f"/v1/configurations/{cid}", headers=headers)
    assert got.status_code == 200 and got.json()["id"] == cid

    patched = client.patch(
        f"/v1/configurations/{cid}",
        json={"spec": {"ps": 300}},
        headers={**headers, "If-Match": str(version)},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["spec"]["ps"] == 300
    assert "ps" in patched.json()["overriddenFields"]

    # cross-tenant → 404
    assert client.get(f"/v1/configurations/{cid}", headers=_bearer(uuid.uuid4())).status_code == 404


def test_api_has_no_list_endpoint(client, db_session):
    """ADR-068 — no standalone surface."""

    r = client.get("/v1/configurations", headers=_bearer(uuid.uuid4()))
    assert r.status_code in (404, 405)


def test_api_manual_create_needs_no_variant(client, db_session):
    tenant_id = uuid.uuid4()
    r = client.post(
        "/v1/configurations",
        json={"source": "manual", "mode": "record", "matchMethod": "manual",
              "spec": {"ps": 54}, "brandDisplayName": "Citroën", "variantName": "2CV"},
        headers={**_bearer(tenant_id), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 201, r.text
    assert r.json()["catalogueMatchStatus"] == "unverified"
    assert r.json()["catalogueVariantId"] is None
