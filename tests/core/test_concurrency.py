import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.concurrency import check_version, require_if_match
from app.core.errors import ConflictError, register_error_handlers


def test_check_version_matches_is_noop():
    check_version(current_version=3, if_match_version=3)  # must not raise


def test_check_version_mismatch_raises_conflict_with_details():
    with pytest.raises(ConflictError) as exc_info:
        check_version(current_version=3, if_match_version=2, entity_name="Widget")
    assert exc_info.value.details == {"currentVersion": 3, "ifMatchVersion": 2}


@pytest.fixture()
def if_match_client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.patch("/widgets/{widget_id}")
    def update(widget_id: str, if_match: int = Depends(require_if_match)):
        return {"ifMatch": if_match}

    return TestClient(app)


def test_require_if_match_missing_header_is_400(if_match_client):
    response = if_match_client.patch("/widgets/1")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_require_if_match_non_integer_header_is_400(if_match_client):
    response = if_match_client.patch("/widgets/1", headers={"If-Match": "not-a-number"})
    assert response.status_code == 400


def test_require_if_match_accepts_quoted_and_plain_integers(if_match_client):
    for header_value in ("3", '"3"'):
        response = if_match_client.patch("/widgets/1", headers={"If-Match": header_value})
        assert response.status_code == 200
        assert response.json() == {"ifMatch": 3}
