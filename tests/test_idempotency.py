import uuid

import pytest

from app.core.errors import ConflictError
from app.services.idempotency import find_cached_response, store_response


def test_find_cached_response_returns_none_when_no_record(db_session):
    result = find_cached_response(
        db_session, tenant_id=uuid.uuid4(), key="key-1", path="/v1/customers", body={"name": "Alice"}
    )
    assert result is None


def test_store_then_find_returns_cached_response(db_session):
    tenant_id = uuid.uuid4()
    body = {"name": "Alice"}
    store_response(
        db_session, tenant_id=tenant_id, key="key-1", path="/v1/customers", body=body,
        response_status=201, response_body={"id": "abc", "name": "Alice"},
    )
    db_session.commit()

    cached = find_cached_response(db_session, tenant_id=tenant_id, key="key-1", path="/v1/customers", body=body)
    assert cached is not None
    assert cached.response_status == 201
    assert cached.response_body == {"id": "abc", "name": "Alice"}


def test_same_key_different_payload_raises_conflict(db_session):
    tenant_id = uuid.uuid4()
    store_response(
        db_session, tenant_id=tenant_id, key="key-1", path="/v1/customers", body={"name": "Alice"},
        response_status=201, response_body={"id": "abc"},
    )
    db_session.commit()

    with pytest.raises(ConflictError):
        find_cached_response(
            db_session, tenant_id=tenant_id, key="key-1", path="/v1/customers", body={"name": "Bob"}
        )


def test_same_key_different_tenant_is_independent(db_session):
    key = "shared-key"
    body = {"name": "Alice"}
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    store_response(
        db_session, tenant_id=tenant_a, key=key, path="/v1/customers", body=body,
        response_status=201, response_body={"id": "a"},
    )
    db_session.commit()

    assert find_cached_response(db_session, tenant_id=tenant_b, key=key, path="/v1/customers", body=body) is None


def test_same_key_different_path_raises_conflict(db_session):
    tenant_id = uuid.uuid4()
    body = {"name": "Alice"}
    store_response(
        db_session, tenant_id=tenant_id, key="key-1", path="/v1/customers", body=body,
        response_status=201, response_body={"id": "a"},
    )
    db_session.commit()

    with pytest.raises(ConflictError):
        find_cached_response(db_session, tenant_id=tenant_id, key="key-1", path="/v1/vehicles", body=body)
