"""The data-export seam (KAN-218; Dealer Administration D-A-31, FR-A-44…46):
"the manager exports everything at any time; offboarding is export,
ten-year archive, deletion".

Cross-cutting on purpose (CLAUDE.md rule 8), like `app.core.ownership`:
platform produces the export and asks this registry —
never a context. Each context declares its tables in its own package
(an `Exporter`, exposed through its `public` module) and the composition root
`app/export_registry.py` registers it. Platform imports no context's models;
no context imports platform's export job.

One declaration drives two things, so they cannot drift apart:

- **the export** — every `ExportedTable` is read with its own `scope` clause
  and written as CSV and JSON, described in `schema.json`;
- **completeness** — every mapped table is declared by exactly one exporter,
  as exported or as excluded with a reason (`tests/test_data_export_completeness.py`):
  a new table fails until someone decides.

Nothing here deletes. The deletion ten years after offboarding is a manual
staff step (Anto, 2026-11-14); platform's reconciliation reports the Betriebe
due for it. The declarations' scopes are the map of the Betrieb's rows that
step will need.

"The Betrieb's last write" (FR-A-45: offboarding needs an acknowledged export
produced after it) is the latest `updated_at`/`created_at` over the exported
tables that count as writes; a table whose stamps move without the Betrieb
writing (a sign-in, a session) says `marks_write=False`.
"""

import base64
import contextlib
import csv
import dataclasses
import datetime as dt
import decimal
import enum
import io
import json
import uuid
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO, Protocol, cast

from sqlalchemy import ColumnElement, Table, func, select
from sqlalchemy.orm import Session

FORMAT_VERSION = 1


@dataclasses.dataclass(frozen=True)
class ExportScope:
    """What one export is about. Platform fills it — it owns the
    organisation — so no context has to ask another:

    - `dealership_id`: the Betrieb (the tenant);
    - `group_id`: its dealer group, for group-owned records (ADR-014);
    - `references`: ids of records another context's exported rows name
      (`{"customer": {…}}`), so a group-owned customer is exported for the
      Betrieb's own relationship only — the customers it created or its
      history names;
    - `group_is_single`: the group holds this one Betrieb only, so every
      group-owned record is its own (a customer that predates provenance too).
    """

    dealership_id: uuid.UUID
    group_id: uuid.UUID
    references: Mapping[str, frozenset[uuid.UUID]] = dataclasses.field(default_factory=dict)
    group_is_single: bool = False

    def referenced(self, kind: str) -> frozenset[uuid.UUID]:
        return self.references.get(kind, frozenset())


ScopeClause = Callable[[ExportScope], ColumnElement[bool]]


def table_of(model: type) -> Table:
    """A mapped class's table, typed as the Table it is."""

    return cast(Table, model.__table__)  # type: ignore[attr-defined]


@dataclasses.dataclass(frozen=True)
class ExportedTable:
    """One table in the export. `scope` selects the Betrieb's rows.
    `omit_columns` never leave the database (none today holds a secret —
    the secret tables are excluded whole). `marks_write=False`: the table's
    stamps move without the Betrieb writing anything."""

    table: Table
    scope: ScopeClause
    omit_columns: frozenset[str] = frozenset()
    marks_write: bool = True
    # False: the Betrieb's rows (its scope selects them) are not written into
    # the archive — a secret, a session; `exclusion_reason` says why.
    in_archive: bool = True
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.in_archive and not self.exclusion_reason:
            raise ValueError(f"{self.table.name}: a table left out of the archive needs its reason")

    @property
    def name(self) -> str:
        return self.table.name


@dataclasses.dataclass(frozen=True)
class ExportDocument:
    """One issued document as PDF (FR-A-44: "with its number and date").
    `render` produces the bytes when the archive is written."""

    path: str
    kind: str
    number: str | None
    issued_at: dt.datetime
    render: Callable[[], bytes]


@dataclasses.dataclass(frozen=True)
class ExportFile:
    """One uploaded file (logos, photos, signatures — Q4 A)."""

    path: str
    content_type: str
    data: bytes


class Exporter(Protocol):
    context: str

    def exported_tables(self) -> Sequence[ExportedTable]: ...

    def excluded_tables(self) -> Mapping[str, str]:
        """Every other table this context maps, with the reason it is not in
        the export (a secret, a cache, the platform's own reference data)."""
        ...

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]: ...

    def documents(self, db: Session, scope: ExportScope) -> Iterable[ExportDocument]: ...

    def files(self, db: Session, scope: ExportScope) -> Iterable[ExportFile]: ...

    def last_write_at(self, db: Session, scope: ExportScope) -> dt.datetime | None: ...


class TableExporter:
    """The common shape: a context lists its tables; reading and the last
    write are generic. A context overrides what it adds — its documents, its
    files, its references."""

    context: str = ""

    def exported_tables(self) -> Sequence[ExportedTable]:
        return ()

    def excluded_tables(self) -> Mapping[str, str]:
        return {}

    def references(self, db: Session, scope: ExportScope) -> Mapping[str, frozenset[uuid.UUID]]:
        return {}

    def documents(self, db: Session, scope: ExportScope) -> Iterable[ExportDocument]:
        return ()

    def files(self, db: Session, scope: ExportScope) -> Iterable[ExportFile]:
        return ()

    def last_write_at(self, db: Session, scope: ExportScope) -> dt.datetime | None:
        latest: dt.datetime | None = None
        for spec in self.exported_tables():
            if not spec.marks_write or not spec.in_archive:
                continue
            stamp = spec.table.c.get("updated_at")
            if stamp is None:
                stamp = spec.table.c.get("created_at")
            if stamp is None:
                continue
            value = db.scalar(select(func.max(stamp)).where(spec.scope(scope)))
            if value is not None and (latest is None or value > latest):
                latest = value
        return latest


class StubExporter(TableExporter):
    """A stub context (aftersales, parts, finance, reporting, compliance)
    owns no table yet and exports nothing — but it registers, so the registry
    is the complete inventory of contexts and a first table there fails the
    completeness test until its exporter declares it."""

    def __init__(self, context: str) -> None:
        self.context = context


_REGISTRY: dict[str, Exporter] = {}


def register_exporter(exporter: Exporter) -> None:
    """Idempotent by context (the composition root may be imported twice)."""

    _REGISTRY[exporter.context] = exporter


def exporters() -> list[Exporter]:
    return [_REGISTRY[context] for context in sorted(_REGISTRY)]


def registered_export_contexts() -> list[str]:
    return sorted(_REGISTRY)


# --- writing the archive --------------------------------------------------------


def plain(value: Any) -> Any:
    """A cell as JSON carries it: ids, dates and amounts as text, an enum's
    value, bytes as base64; lists and objects stay structured."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(v) for v in value]
    return str(value)


def _csv_cell(value: Any) -> str:
    cell = plain(value)
    if cell is None:
        return ""
    if isinstance(cell, dict | list):
        return json.dumps(cell, ensure_ascii=False, sort_keys=True)
    if isinstance(cell, bool):
        return "true" if cell else "false"
    return str(cell)


def _column_type(column) -> str:
    try:
        return str(column.type.python_type.__name__)
    except NotImplementedError:
        return type(column.type).__name__


# KAN-715: rows are fetched from the database in batches of this many
# (a server-side cursor on Postgres), never one whole table in a list.
ROW_BATCH = 1000
# The archive entries are written in chunks of this many bytes at most.
_COPY_CHUNK = 1024 * 1024


def read_rows(db: Session, spec: ExportedTable, scope: ExportScope) -> tuple[list[str], Iterator[dict[str, Any]]]:
    """The column names and the Betrieb's rows of one table, oldest first —
    streamed in batches of `ROW_BATCH` (KAN-715): a large table is never
    held whole. Read the rows inside the caller's one snapshot (ADR-166);
    iterating twice reads the same rows."""

    columns = [column for column in spec.table.c if column.name not in spec.omit_columns]
    order = [spec.table.c[name] for name in ("created_at", "id") if name in spec.table.c]
    statement = (
        select(*columns).where(spec.scope(scope)).order_by(*order).execution_options(yield_per=ROW_BATCH)
    )

    def rows() -> Iterator[dict[str, Any]]:
        for row in db.execute(statement):
            yield dict(row._mapping)

    return [column.name for column in columns], rows()


@dataclasses.dataclass(frozen=True)
class ArchiveSummary:
    byte_size: int
    row_counts: dict[str, int]
    document_count: int
    file_count: int


def _write_csv(archive: "_UniqueEntries", name: str, names: list[str], rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with archive.open_text(name) as text:
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(names)
        for row in rows:
            writer.writerow([_csv_cell(row[column]) for column in names])
            count += 1
    return count


def _write_json(archive: "_UniqueEntries", name: str, names: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    """The rows as one JSON array, written row by row — the same text
    `json.dumps(rows, ensure_ascii=False, indent=1)` gives, never built
    whole."""

    with archive.open_text(name) as text:
        first = True
        for row in rows:
            item = json.dumps({column: plain(row[column]) for column in names}, ensure_ascii=False, indent=1)
            text.write("[\n" if first else ",\n")
            text.write("\n".join(" " + line for line in item.split("\n")))
            first = False
        text.write("[]" if first else "\n]")


def _entities(registered: Sequence[Exporter]) -> Iterator[tuple[Exporter, ExportedTable]]:
    for exporter in registered:
        for spec in exporter.exported_tables():
            if spec.in_archive:
                yield exporter, spec


class DuplicateArchiveEntry(ValueError):
    """Two entries of one export share a name: a ZIP keeps both, and every
    reader opens one of them, so the other would be lost."""


class _UniqueEntries:
    """The archive's one writer: refuses a name it has written already
    (KAN-218, Q4 A — a location's logo once overwrote the Betrieb's)."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._archive = archive
        self._names: set[str] = set()

    def _claim(self, name: str) -> None:
        if name in self._names:
            raise DuplicateArchiveEntry(f"The export would hold {name!r} twice.")
        self._names.add(name)

    def writestr(self, name: str, data: str | bytes) -> None:
        self._claim(name)
        # zip64 for an entry that may pass 2 GiB (KAN-715: nothing caps a file's size).
        with self._archive.open(name, "w", force_zip64=True) as entry:
            payload = data.encode("utf-8") if isinstance(data, str) else data
            for start in range(0, len(payload), _COPY_CHUNK):
                entry.write(payload[start : start + _COPY_CHUNK])

    @contextlib.contextmanager
    def open_text(self, name: str) -> Iterator[io.TextIOWrapper]:
        """One entry written as UTF-8 text while it is produced (KAN-715)."""

        self._claim(name)
        with self._archive.open(name, "w", force_zip64=True) as entry:
            text = io.TextIOWrapper(entry, encoding="utf-8", newline="")
            try:
                yield text
            finally:
                text.flush()
                text.detach()


def write_archive(
    db: Session,
    *,
    scope: ExportScope,
    produced_at: dt.datetime,
    header: Mapping[str, Any],
    target: BinaryIO,
    registered: Sequence[Exporter] | None = None,
) -> ArchiveSummary:
    """The export package (FR-A-44): per entity `data/{context}/{table}.csv`
    and `.json`, every issued document under `documents/` with an index of
    numbers and dates, every uploaded file under `files/`, and `schema.json`
    describing each entity's columns and what was left out and why. A ZIP:
    open formats inside an open container. A name written twice is refused
    (`DuplicateArchiveEntry`), so the production fails, never loses a file.

    KAN-715: the ZIP is written into `target` — the caller's temporary file —
    entry by entry, and each table's rows are read in batches twice (once
    for the CSV, once for the JSON, both in the caller's one snapshot), so
    neither the archive nor a whole table is ever held in memory."""

    exporters_ = list(registered if registered is not None else exporters())
    schema_entities: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    row_counts: dict[str, int] = {}
    document_index: list[dict[str, Any]] = []
    file_index: list[dict[str, Any]] = []
    start = target.tell()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zipped:
        archive = _UniqueEntries(zipped)
        for exporter, spec in _entities(exporters_):
            base = f"data/{exporter.context}/{spec.name}"
            names, rows = read_rows(db, spec, scope)
            count = _write_csv(archive, f"{base}.csv", names, rows)
            _names, rows = read_rows(db, spec, scope)
            _write_json(archive, f"{base}.json", names, rows)
            row_counts[spec.name] = count
            schema_entities.append(
                {
                    "context": exporter.context,
                    "entity": spec.name,
                    "files": [f"{base}.csv", f"{base}.json"],
                    "rows": count,
                    "columns": [
                        {
                            "name": column.name,
                            "type": _column_type(column),
                            "nullable": bool(column.nullable),
                            "primaryKey": bool(column.primary_key),
                            "description": column.comment,
                        }
                        for column in spec.table.c
                        if column.name in names
                    ],
                }
            )
        for exporter in exporters_:
            for spec in exporter.exported_tables():
                if not spec.in_archive:
                    excluded.append(
                        {"context": exporter.context, "entity": spec.name, "reason": str(spec.exclusion_reason)}
                    )
            for table_name, reason in sorted(exporter.excluded_tables().items()):
                excluded.append({"context": exporter.context, "entity": table_name, "reason": reason})
            for document in exporter.documents(db, scope):
                archive.writestr(document.path, document.render())
                document_index.append(
                    {
                        "context": exporter.context,
                        "kind": document.kind,
                        "number": document.number,
                        "issuedAt": plain(document.issued_at),
                        "file": document.path,
                    }
                )
            for stored in exporter.files(db, scope):
                archive.writestr(stored.path, stored.data)
                file_index.append(
                    {"context": exporter.context, "contentType": stored.content_type, "file": stored.path}
                )
        archive.writestr("documents/index.json", json.dumps(document_index, ensure_ascii=False, indent=1))
        archive.writestr("files/index.json", json.dumps(file_index, ensure_ascii=False, indent=1))
        archive.writestr(
            "schema.json",
            json.dumps(
                {
                    "formatVersion": FORMAT_VERSION,
                    "producedAt": plain(produced_at),
                    **{key: plain(value) for key, value in header.items()},
                    "entities": schema_entities,
                    "excluded": excluded,
                    "documents": len(document_index),
                    "files": len(file_index),
                },
                ensure_ascii=False,
                indent=1,
            ),
        )
    target.flush()
    return ArchiveSummary(
        byte_size=target.tell() - start,
        row_counts=row_counts,
        document_count=len(document_index),
        file_count=len(file_index),
    )
