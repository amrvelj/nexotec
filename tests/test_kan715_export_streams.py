"""KAN-715: the data export never holds the whole archive, nor a whole table,
in the worker's memory. `write_archive` writes the ZIP into the caller's
temporary file entry by entry and reads each table in batches; the object
store takes the file in chunks (`put_file`). The archive's layout and text
are unchanged.

On Postgres: a Betrieb with a large table, the peak of Python's allocations
while the archive is written measured with `tracemalloc`.
"""

import datetime as dt
import json
import tempfile
import tracemalloc
import uuid
import zipfile

from sqlalchemy import insert

from app.core.base import utcnow
from app.core.export import ExportedTable, ExportScope, TableExporter, table_of, write_archive
from app.core.object_store import LocalDirectoryObjectStore
from tests.demo_models import DemoWidget

LARGE = 40_000
# The table's rows alone come to some 40 MB as Python objects and about
# 20 MB as CSV and JSON text; streamed, the writer holds a batch at a time.
PEAK_LIMIT = 12 * 1024 * 1024


class _Widgets(TableExporter):
    context = "probe"

    def exported_tables(self):
        return (ExportedTable(table=table_of(DemoWidget), scope=lambda scope: DemoWidget.tenant_id == scope.dealership_id),)


def _seed(db_session, tenant_id: uuid.UUID, count: int) -> None:
    created = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    batch = 5_000
    for start in range(0, count, batch):
        db_session.execute(
            insert(DemoWidget),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "name": f"Widget {n:06d} " + "x" * 80,
                    "version": 1,
                    "created_at": created + dt.timedelta(seconds=n),
                    "updated_at": created + dt.timedelta(seconds=n),
                }
                for n in range(start, min(start + batch, count))
            ],
        )
    db_session.commit()


def _write(db_session, tenant_id: uuid.UUID, target) -> None:
    write_archive(
        db_session,
        scope=ExportScope(dealership_id=tenant_id, group_id=uuid.uuid4()),
        produced_at=utcnow(),
        header={},
        target=target,
        registered=[_Widgets()],
    )


def test_a_large_betriebs_archive_is_complete_while_memory_stays_bounded(db_session):
    tenant_id = uuid.uuid4()
    _seed(db_session, tenant_id, LARGE)
    _seed(db_session, uuid.uuid4(), 10)  # another Betrieb's rows stay out

    with tempfile.TemporaryFile() as target:
        tracemalloc.start()
        try:
            _write(db_session, tenant_id, target)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert peak < PEAK_LIMIT, f"peak {peak / 1e6:.1f} MB while writing the archive"
        target.seek(0)
        with zipfile.ZipFile(target) as archive:
            with archive.open("data/probe/demo_widget.csv") as csv_file:
                lines = sum(1 for _ in csv_file)
            assert lines == LARGE + 1  # the header and every row
            schema = json.loads(archive.read("schema.json"))
            assert schema["entities"][0]["rows"] == LARGE
            rows = json.loads(archive.read("data/probe/demo_widget.json"))
            assert len(rows) == LARGE
            assert rows[0]["name"].startswith("Widget 000000 ")
            assert rows[-1]["name"].startswith(f"Widget {LARGE - 1:06d} ")


def test_the_json_text_is_what_json_dumps_wrote_before(db_session):
    tenant_id = uuid.uuid4()
    _seed(db_session, tenant_id, 3)

    with tempfile.TemporaryFile() as target:
        _write(db_session, tenant_id, target)
        target.seek(0)
        with zipfile.ZipFile(target) as archive:
            text = archive.read("data/probe/demo_widget.json").decode("utf-8")
            csv_text = archive.read("data/probe/demo_widget.csv").decode("utf-8")

    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=1)
    assert csv_text.splitlines()[0] == "name,id,tenant_id,version,created_at,updated_at,created_by,updated_by"
    assert len(csv_text.splitlines()) == 4


def test_an_empty_table_is_an_empty_array(db_session):
    with tempfile.TemporaryFile() as target:
        _write(db_session, uuid.uuid4(), target)
        target.seek(0)
        with zipfile.ZipFile(target) as archive:
            assert archive.read("data/probe/demo_widget.json") == b"[]"
            assert archive.read("data/probe/demo_widget.csv") == b"name,id,tenant_id,version,created_at,updated_at,created_by,updated_by\n"


def test_the_object_store_takes_a_file_in_chunks(tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    payload = b"z" * (3 * 1024 * 1024 + 17)
    source = tmp_path / "archive.zip"
    source.write_bytes(payload)

    with source.open("rb") as handle:
        store.put_file("dealerships/x/exports/y.zip", handle, content_type="application/zip")

    stored = store.get("dealerships/x/exports/y.zip")
    assert stored is not None
    assert (stored.data == payload, stored.content_type) == (True, "application/zip")
