"""C-C (KAN-41) exit criterion 5 — "A test proves a configuration cannot
write `vehicle_mdm`. Not a convention; a test." (ADR-070.)

Two layers:
  1. Source scan — the configuration service / API never construct a
     `VehicleMdm`, never `db.add` one, and only reach vehicle-mdm through
     read helpers.
  2. Runtime guard — flag any `VehicleMdm` that reaches `Session.add` /
     `Session.merge`, then drive every configuration write path and assert
     none fires.
"""

import ast
import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import (
    ConfigurationMatchMethod,
    ConfigurationMatchStatus,
    ConfigurationMode,
    ConfigurationSource,
)
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.schemas.configuration import (
    ConfigurationCreate,
    ConfigurationOptionInput,
    ConfigurationUpdate,
)
from app.vehicle.schemas.spec_block import VehicleSpecBlockInput
from app.vehicle.services import configuration as configuration_service

_REPO = Path(__file__).resolve().parent.parent.parent
_SCANNED = [
    _REPO / "app" / "vehicle" / "services" / "configuration.py",
    _REPO / "app" / "vehicle" / "api" / "configuration.py",
]


def test_the_scanned_files_still_exist():
    for path in _SCANNED:
        assert path.exists(), path


@pytest.mark.parametrize("path", _SCANNED, ids=lambda p: p.name)
def test_configuration_source_never_constructs_or_adds_a_vehicle_mdm(path: Path):
    tree = ast.parse(path.read_text())

    # No `VehicleMdm(...)` construction anywhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name != "VehicleMdm", f"{path.name} constructs a VehicleMdm"

    # The only vehicle_mdm symbol allowed in is the read helper.
    src = path.read_text()
    assert "update_vehicle_mdm" not in src
    assert "create_or_get_vehicle_mdm" not in src
    assert "create_vehicle_mdm" not in src


def _variant(db: Session) -> ModelVariant:
    brand = Brand(code="x", display_name="X")
    db.add(brand)
    db.flush()
    group = ModelGroup(brand_id=brand.id, name="G")
    db.add(group)
    db.flush()
    v = ModelVariant(model_group_id=group.id, name="X 1.0", model_year_from=2022, vehicle_kind="passenger_car", ps=110)
    db.add(v)
    db.flush()
    return v


def test_no_configuration_write_path_ever_adds_a_vehicle_mdm(db_session):
    flagged: list[str] = []

    @event.listens_for(db_session, "before_flush")
    def _guard(session, _flush_context, _instances):
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, VehicleMdm):
                flagged.append(repr(obj))

    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    variant = _variant(db_session)
    db_session.commit()

    # provider create (copies the spec block)
    config = configuration_service.create_configuration(
        db_session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER,
            mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE,
            catalogue_variant_id=variant.id,
        ),
    )

    # manual create with a VIN that does NOT resolve (must not create an MDM row)
    manual = configuration_service.create_configuration(
        db_session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        data=ConfigurationCreate(
            source=ConfigurationSource.MANUAL,
            mode=ConfigurationMode.RECORD,
            match_method=ConfigurationMatchMethod.MANUAL,
            vin="WVWZZZ1KZAW000001",
            first_registration_date=dt.date(2015, 6, 1),
            spec=VehicleSpecBlockInput(ps=90),
        ),
    )
    assert manual.vehicle_id is None
    assert db_session.query(VehicleMdm).count() == 0

    # update spec + match-status transition
    configuration_service.update_configuration(
        db_session,
        configuration=configuration_service.get_configuration_or_404(
            db_session, tenant_id=tenant_id, configuration_id=config.id
        ),
        actor_id=actor_id,
        data=ConfigurationUpdate(spec=VehicleSpecBlockInput(ps=125), catalogue_match_status=ConfigurationMatchStatus.BEST_MATCH_CONFIRMED),
    )

    # copy + options
    configuration_service.copy_configuration(
        db_session,
        source=configuration_service.get_configuration_or_404(
            db_session, tenant_id=tenant_id, configuration_id=config.id
        ),
        actor_id=actor_id,
    )
    configuration_service.replace_options(
        db_session,
        configuration=configuration_service.get_configuration_or_404(
            db_session, tenant_id=tenant_id, configuration_id=manual.id
        ),
        actor_id=actor_id,
        options=[ConfigurationOptionInput(description="Tow bar", price=Decimal("950.00"))],
    )

    assert flagged == [], f"a VehicleMdm reached a flush from a configuration path: {flagged}"
    assert db_session.query(VehicleMdm).count() == 0
