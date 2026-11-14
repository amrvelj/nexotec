"""Helpers for the KAN-218 tests: an object store under a temp dir, a Betrieb
with its manager, requesting and producing an export the way the worker
does, and reading the archive back."""

import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy.orm import Session, sessionmaker

import app.export_registry  # noqa: F401 - every context's exporter, as app.main and app.worker register them
from app.core import object_store
from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.main import app as fastapi_app
from app.platform.services import data_export
from tests.identity_support import platform_admin_headers
from tests.membership_support import bearer, create_dealership, create_user, token


@pytest.fixture(name="export_store")
def export_store_fixture(tmp_path, monkeypatch):
    """One store for the API, the worker's production and the renderer."""

    store = LocalDirectoryObjectStore(tmp_path / "objects")
    monkeypatch.setattr(object_store, "_store", store)
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    yield store
    fastapi_app.dependency_overrides.pop(get_object_store, None)


def staff() -> dict[str, str]:
    return platform_admin_headers()


def idem(headers: dict[str, str], **extra: str) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(uuid.uuid4()), **extra}


def betrieb_with_manager(client, legal_name: str = "Garage Muster AG", *, group_of: str | None = None):
    dealership_id = create_dealership(client, legal_name, group_of=group_of)
    manager = create_user(client, dealership_id, is_dealer_manager=True)
    headers = bearer(token(tenant_id=dealership_id, user_id=manager["id"], is_dealer_manager=True))
    return dealership_id, manager, headers


def request_export(client, dealership_id: str, headers: dict[str, str]) -> dict:
    response = client.post(f"/v1/dealerships/{dealership_id}/exports", headers=idem(headers))
    assert response.status_code == 202, response.text
    return response.json()


def produce(engine, export_id: str) -> None:
    """What the worker's consumer does, on a session of its own."""

    session: Session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        data_export.produce(session, export_id=uuid.UUID(export_id))
        session.commit()
    finally:
        session.close()


def get_export(client, dealership_id: str, export_id: str, headers: dict[str, str]) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{export_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def acknowledge(client, dealership_id: str, export: dict, headers: dict[str, str]):
    return client.post(
        f"/v1/dealerships/{dealership_id}/exports/{export['id']}/acknowledge",
        headers=idem(headers, **{"If-Match": str(export["version"])}),
    )


def exported_and_acknowledged(client, engine, dealership_id: str, headers: dict[str, str]) -> dict:
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])
    ready = get_export(client, dealership_id, job["id"], headers)
    response = acknowledge(client, dealership_id, ready, headers)
    assert response.status_code == 200, response.text
    return response.json()


def archive_of(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def rows_in(archive: zipfile.ZipFile, context: str, table: str) -> list[dict]:
    return json.loads(archive.read(f"data/{context}/{table}.json"))


def download(client, dealership_id: str, export_id: str, headers: dict[str, str]) -> zipfile.ZipFile:
    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{export_id}/download", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "no-store" in response.headers["cache-control"]
    return archive_of(response.content)
