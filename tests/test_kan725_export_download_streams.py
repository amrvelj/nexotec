"""KAN-725 (KAN-715 follow-up): the export download never reads the whole
archive into the web process's memory. The object store opens the file for a
streamed read (`ObjectStore.open`), the service hands that on after auditing
the download, and the route answers with a `StreamingResponse` of chunks. The
authenticated GET, its headers, the audit row per download and the seven-day
window are unchanged.

On Postgres: an archive of several MB downloaded byte for byte, and the peak of
Python's allocations while the service's download is read measured with
`tracemalloc`.
"""

import asyncio
import hashlib
import os
import tempfile
import tracemalloc
import uuid

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.object_store import LocalDirectoryObjectStore
from app.main import app as fastapi_app
from app.platform.models.export_job import ExportJob
from app.platform.services import data_export
from tests.data_export_support import (
    betrieb_with_manager,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    produce,
    request_export,
)

SIZE = 24 * 1024 * 1024
# Streamed, the reader holds one 1 MiB chunk at a time; read whole, the
# archive alone would be SIZE.
PEAK_LIMIT = 4 * 1024 * 1024


def _ready_export_with_a_large_file(client, engine, db_session, store) -> tuple[str, ExportJob, dict, str]:
    """A produced export whose stored file is replaced by SIZE random bytes
    (the archive's content does not matter to the download); returns the
    Betrieb, the job row, the manager's headers and the bytes' SHA-256."""

    dealership_id, _manager, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])
    row = db_session.get(ExportJob, uuid.UUID(job["id"]))
    digest = hashlib.sha256()
    with tempfile.TemporaryFile() as large:
        for _ in range(SIZE // (1024 * 1024)):
            chunk = os.urandom(1024 * 1024)
            digest.update(chunk)
            large.write(chunk)
        large.seek(0)
        store.put_file(row.object_key, large, content_type=data_export.CONTENT_TYPE)
    return dealership_id, row, headers, digest.hexdigest()


def _downloads(db_session, job_id: uuid.UUID) -> int:
    db_session.expire_all()
    return len(
        list(db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == job_id, AuditEvent.action == "downloaded")))
    )


def test_a_large_archive_downloads_byte_for_byte_with_its_headers(client, engine, db_session, export_store):
    dealership_id, row, headers, expected = _ready_export_with_a_large_file(client, engine, db_session, export_store)

    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{row.id}/download", headers=headers)

    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == expected
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(SIZE)
    assert response.headers["content-disposition"] == f'attachment; filename="nexotec-export-{row.id}.zip"'
    assert _downloads(db_session, row.id) == 1


def test_the_download_is_read_in_chunks_with_a_bounded_peak(client, engine, db_session, export_store):
    _dealership_id, row, _headers, expected = _ready_export_with_a_large_file(client, engine, db_session, export_store)
    digest = hashlib.sha256()

    tracemalloc.start()
    try:
        opened = data_export.download(db_session, job=row, actor_id=uuid.uuid4(), store=export_store)
        for chunk in opened.chunks():
            digest.update(chunk)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert digest.hexdigest() == expected
    assert opened.stream.closed
    assert peak < PEAK_LIMIT, f"peak {peak} bytes while downloading {SIZE} bytes"


def _asgi_get(path: str, headers: dict[str, str]) -> tuple[int, list[int], str]:
    """The route called over raw ASGI — the TestClient would buffer the body
    — with a `send` that keeps only each body message's size; returns the
    status, those sizes and the bytes' SHA-256."""

    status: list[int] = []
    sizes: list[int] = []
    digest = hashlib.sha256()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }

    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client never disconnects
        raise AssertionError("unreachable")

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            sizes.append(len(body))
            digest.update(body)

    asyncio.run(fastapi_app(scope, receive, send))
    return status[0], sizes, digest.hexdigest()


def test_the_route_sends_the_archive_in_chunks_never_whole(client, engine, db_session, export_store):
    dealership_id, row, headers, expected = _ready_export_with_a_large_file(client, engine, db_session, export_store)

    status, sizes, digest = _asgi_get(f"/v1/dealerships/{dealership_id}/exports/{row.id}/download", headers)

    assert status == 200
    assert digest == expected
    assert sum(sizes) == SIZE
    # Streamed: many body messages, none larger than one store chunk.
    assert len([size for size in sizes if size]) >= SIZE // (1024 * 1024)
    assert max(sizes) <= 1024 * 1024


def test_the_store_opens_nothing_for_a_missing_key(tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")

    assert store.open("dealerships/x/exports/missing.zip") is None


def test_a_missing_file_is_still_409_and_audits_no_download(client, engine, db_session, export_store):
    dealership_id, row, headers, _expected = _ready_export_with_a_large_file(client, engine, db_session, export_store)
    export_store.delete(row.object_key)

    response = client.get(f"/v1/dealerships/{dealership_id}/exports/{row.id}/download", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "export_file_missing"
    assert _downloads(db_session, row.id) == 0
