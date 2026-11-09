"""KAN-549 (ADR-113): migration `3b3faa39c5e2` retires the `dat` provider —
its row, every connection of it with their secret references and
entitlements, and the `vin_decode` cache rows the retired derivation wrote
on other connections — and leaves everything else, the `vin` licence-option
rows and the call log included.

The migration's real `upgrade()` runs on Postgres over the create_all schema
(`tests/test_kan86_value_migrations.py`'s pattern).
"""

import importlib.util
import os
import pathlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from app.core.base import utcnow
from app.integration.models.call_log import CallStatus, IntegrationCallLog
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.entitlement import EntitlementSource, IntegrationEntitlement
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef, SecretSlot

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "integration"
_postgres_only = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _load():
    (path,) = sorted(_VERSIONS.glob("3b3faa39c5e2_*.py"))
    spec = importlib.util.spec_from_file_location("kan549_retire_dat", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load()


def _provider(db, code: str, capability_codes: list[str]) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code=code, category="vehicle_data", display_name=code, auth_type="none",
        required_secret_slots=[], capability_codes=capability_codes,
    )
    db.add(provider)
    db.flush()
    return provider


def _connection(db, provider: IntegrationProvider, tenant_id: uuid.UUID) -> IntegrationConnection:
    connection = IntegrationConnection(
        tenant_id=tenant_id, provider_id=provider.id, display_name=provider.provider_code,
        environment=ConnectionEnvironment.SANDBOX, config={}, scope=ConnectionScope.TENANT,
    )
    db.add(connection)
    db.flush()
    return connection


def _entitlement(db, connection: IntegrationConnection, code: str, granted: bool) -> None:
    db.add(IntegrationEntitlement(
        connection_id=connection.id, capability_code=code, granted=granted, source=EntitlementSource.PROBED, checked_at=utcnow(),
    ))


@_postgres_only
def test_the_dat_provider_its_connections_and_the_vin_decode_cache_go_and_nothing_else(engine, db_session):
    tenant_id = uuid.uuid4()
    dat = _provider(db_session, "dat", ["vin", "vin_ident_db"])
    identapi = _provider(db_session, "auto_i_dat", ["ident_basis", "vin"])
    dat_connection = _connection(db_session, dat, tenant_id)
    identapi_connection = _connection(db_session, identapi, tenant_id)
    _entitlement(db_session, dat_connection, "vin", True)
    _entitlement(db_session, identapi_connection, "vin_decode", True)  # the retired derivation's cache
    _entitlement(db_session, identapi_connection, "vin", False)  # the licence option: kept
    _entitlement(db_session, identapi_connection, "ident_basis", True)
    db_session.add(IntegrationSecretRef(connection_id=dat_connection.id, slot=SecretSlot.PASSWORD, secret_ref="ref/dat"))
    db_session.add(IntegrationCallLog(
        connection_id=dat_connection.id, connection_label="dat", tenant_id=tenant_id,
        capability="system_watermark", status=CallStatus.ERROR, duration_ms=1, correlation_id=uuid.uuid4(),
    ))
    db_session.commit()
    dat_id, identapi_id = dat_connection.id, identapi_connection.id

    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _MIGRATION.upgrade()

    db_session.expire_all()
    assert db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == "dat")) is None
    assert db_session.get(IntegrationConnection, dat_id) is None
    assert db_session.scalar(select(func.count()).select_from(IntegrationSecretRef)) == 0
    remaining = {
        (e.connection_id, e.capability_code, e.granted) for e in db_session.scalars(select(IntegrationEntitlement))
    }
    assert remaining == {(identapi_id, "vin", False), (identapi_id, "ident_basis", True)}
    assert db_session.get(IntegrationConnection, identapi_id) is not None
    # the call log outlives a deleted connection (KAN-470)
    assert db_session.scalar(select(func.count()).select_from(IntegrationCallLog)) == 1


@_postgres_only
def test_the_upgrade_is_harmless_where_no_dat_provider_was_ever_seeded(engine, db_session):
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _MIGRATION.upgrade()
    assert db_session.scalar(select(func.count()).select_from(IntegrationProvider)) == 0
