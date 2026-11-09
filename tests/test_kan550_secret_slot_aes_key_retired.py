"""KAN-550 — integration-chain migration `db5f1414cbce` retires the `aes_key`
secret slot.

The CHECK's wording against the model is `tests/test_migrated_enum_checks.py`'s
job (a real `alembic upgrade heads`). Here: the upgrade refuses — naming the
connections — while any `aes_key` reference is left, rather than dropping a
pointer to secret material it cannot reach, and passes when none is.

The test schema is built from the models, whose CHECK already forbids
`aes_key`, so the test drops that CHECK to stage the pre-upgrade state (DDL:
the schema is rebuilt before the next test, `tests/conftest.py`).
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.secret_ref import SecretSlot
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from tests.test_integration_registry import _make_provider

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "integration"


def _migration():
    (path,) = sorted(_VERSIONS.glob("db5f1414cbce_*.py"))
    spec = importlib.util.spec_from_file_location("kan550_secret_slot_aes_key_retired", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connection(db_session):
    return connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=_make_provider(db_session).id, display_name="Test", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )


def _insert_ref(db_session, connection_id, slot: str) -> None:
    db_session.execute(
        text(
            "INSERT INTO integration_secret_ref (id, connection_id, slot, secret_ref, created_at, updated_at) "
            "VALUES (:id, :connection_id, :slot, :ref, now(), now())"
        ),
        {"id": uuid.uuid4(), "connection_id": connection_id, "slot": slot, "ref": f"/integrations/{connection_id}/{slot}"},
    )


def test_the_model_no_longer_names_the_slot():
    assert "aes_key" not in {slot.value for slot in SecretSlot}


def test_the_upgrade_refuses_while_an_aes_key_reference_is_left_and_names_its_connection(db_session):
    connection = _connection(db_session)
    db_session.execute(text("ALTER TABLE integration_secret_ref DROP CONSTRAINT ck_integration_secret_ref_slot_enum"))
    _insert_ref(db_session, connection.id, "aes_key")
    _insert_ref(db_session, connection.id, "client_secret")
    db_session.commit()

    with pytest.raises(RuntimeError, match=str(connection.id)):
        _migration().refuse_if_the_slot_is_held(db_session.connection())
    db_session.rollback()


def test_the_upgrade_passes_when_no_aes_key_reference_is_left(db_session):
    connection = _connection(db_session)
    _insert_ref(db_session, connection.id, "client_secret")
    db_session.commit()

    _migration().refuse_if_the_slot_is_held(db_session.connection())  # no raise
    db_session.rollback()
