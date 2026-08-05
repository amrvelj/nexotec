import datetime as dt
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_error_handlers
from app.core.pagination import CursorPosition, decode_cursor, encode_cursor, page_params


def test_cursor_roundtrip():
    position = CursorPosition(created_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), id=uuid.uuid4())
    decoded = decode_cursor(encode_cursor(position))
    assert decoded == position


def test_decode_cursor_rejects_garbage():
    from app.core.errors import BadRequestError

    with pytest.raises(BadRequestError):
        decode_cursor("not-valid-base64-json!!!")


@pytest.fixture()
def page_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/items")
    def list_items(params=Depends(page_params)):
        return {"limit": params.limit, "hasCursor": params.cursor is not None}

    return TestClient(app)


def test_default_limit_applied(page_client):
    response = page_client.get("/items")
    assert response.status_code == 200
    assert response.json()["limit"] == 50


def test_limit_over_max_is_422(page_client):
    response = page_client.get("/items", params={"limit": 101})
    assert response.status_code == 422


def test_limit_under_min_is_422(page_client):
    response = page_client.get("/items", params={"limit": 0})
    assert response.status_code == 422


def test_valid_cursor_param_is_decoded(page_client):
    cursor = encode_cursor(CursorPosition(created_at=dt.datetime.now(dt.timezone.utc), id=uuid.uuid4()))
    response = page_client.get("/items", params={"cursor": cursor})
    assert response.status_code == 200
    assert response.json()["hasCursor"] is True


def test_malformed_cursor_param_is_400(page_client):
    response = page_client.get("/items", params={"cursor": "!!!not-valid"})
    assert response.status_code == 400
