import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableEntityError,
    register_error_handlers,
)


class Body(BaseModel):
    name: str


@pytest.fixture()
def error_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/bad-request")
    def bad_request():
        raise BadRequestError("bad input", details={"field": "x"})

    @app.get("/unauthorized")
    def unauthorized():
        raise UnauthorizedError("no token")

    @app.get("/forbidden")
    def forbidden():
        raise ForbiddenError("nope")

    @app.get("/not-found")
    def not_found():
        raise NotFoundError("missing")

    @app.get("/conflict")
    def conflict():
        raise ConflictError("stale")

    @app.get("/unprocessable")
    def unprocessable():
        raise UnprocessableEntityError("bad semantics")

    @app.post("/validate")
    def validate(body: Body):
        return body

    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    [
        ("/bad-request", 400, "bad_request"),
        ("/unauthorized", 401, "unauthorized"),
        ("/forbidden", 403, "forbidden"),
        ("/not-found", 404, "not_found"),
        ("/conflict", 409, "conflict"),
        ("/unprocessable", 422, "unprocessable_entity"),
    ],
)
def test_app_error_shape(error_client, path, expected_status, expected_code):
    response = error_client.get(path)
    assert response.status_code == expected_status
    body = response.json()
    assert body["error"]["code"] == expected_code
    assert "message" in body["error"]


def test_bad_request_includes_details(error_client):
    response = error_client.get("/bad-request")
    assert response.json()["error"]["details"] == {"field": "x"}


def test_pydantic_validation_error_uses_same_envelope(error_client):
    response = error_client.post("/validate", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "unprocessable_entity"
    assert "errors" in body["error"]["details"]
