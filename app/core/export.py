"""The data-export seam (KAN-218; Dealer Administration D-A-31, FR-A-44…46):
"the manager exports everything at any time; offboarding is export,
ten-year archive, deletion".

Cross-cutting on purpose (CLAUDE.md rule 8), like `app.core.ownership`:
platform produces the export and runs the deletion, and asks this registry —
never a context. Each context declares its tables in its own package
(an `Exporter`, exposed through its `public` module) and the composition root
`app/export_registry.py` registers it. Platform imports no context's models;
no context imports platform's export job.

One declaration drives three things, so they cannot drift apart:

- **the export** — every `ExportedTable` is read with its own `scope` clause
  and written as CSV and JSON, described in `schema.json`;
- **completeness** — every mapped table is declared by exactly one exporter,
  as exported or as excluded with a reason (`tests/test_data_export_completeness.py`):
  a new table fails until someone decides;
- **the deletion after the retention period** — `TableExporter.erase` deletes
  the exported tables in reverse foreign-key order, on a session of its own
  that it commits (ADR-047), except the tables declared `erase=False` with
  their reason.

"The Betrieb's last write" (FR-A-45: offboarding needs an acknowledged export
produced after it) is the latest `updated_at`/`created_at` over the exported
tables that count as writes; a table whose stamps move without the Betrieb
writing (a sign-in, a session) says `marks_write=False`.
"""

import base64
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
from typing import Any, Protocol, cast

from sqlalchemy import ColumnElement, Table, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import sort_tables

FORMAT_VERSION = 1


@dataclasses.dataclass(frozen=True)
class ExportScope:
    """What one export or deletion is about. Platform fills it — it owns the
    organisation — so no context has to ask another:

    - `dealership_id`: the Betrieb (the tenant);
    - `group_id`: its dealer group, for group-owned records (ADR-014);
    - `references`: ids of records another context's exported rows name
      (`{"customer": {…}}`), so a group-owned customer is exported for the
      Betrieb's own relationship only — the customers it created or its
      history names;
    - `group_is_single`: the group holds this one Betrieb only, so every
      group-owned record is its own (a customer that predates provenance too);
    - `sole_in_group`: at deletion, no other Betrieb of the group still holds
      data (not yet erased) — only then are group-owned records deleted.
    """

    dealership_id: uuid.UUID
    group_id: uuid.UUID
    references: Mapping[str, frozenset[uuid.UUID]] = dataclasses.field(default_factory=dict)
    group_is_single: bool = False
    sole_in_group: bool = False

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
    the secret tables are excluded whole). `erase=False` keeps the rows at
    deletion; `keep_reason` then says why. `marks_write=False`: the table's
    stamps move without the Betrieb writing anything."""

    table: Table
    scope: ScopeClause
    omit_columns: frozenset[str] = frozenset()
    erase: bool = True
    keep_reason: str | None = None
    marks_write: bool = True
    # Erased only when `ExportScope.sole_in_group` (group-owned records, ADR-014).
    group_owned: bool = False
    # The rows the deletion removes, when they are not the exported ones: a
    # group-owned table exports the Betrieb's relationship but, once no
    # sister holds data, deletes the whole group's rows.
    erase_scope: ScopeClause | None = None
    # Tables whose rows this table's scope reads (a sub-select without a
    # foreign key): their rows go after this table's at deletion.
    depends_on: tuple[Table, ...] = ()
    # False: not written into the archive (a secret, a session), only deleted
    # after the retention period; `exclusion_reason` says why it stays out.
    in_archive: bool = True
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.erase and not self.keep_reason:
            raise ValueError(f"{self.table.name}: a table kept at deletion needs its reason")
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


@dataclasses.dataclass(frozen=True)
class ErasureResult:
    context: str
    rows_deleted: int


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

    def erase(self, session_factory: Callable[[], Session], scope: ExportScope) -> ErasureResult:
        """Deletes the Betrieb's rows on a session of its own and commits
        (ADR-047). Idempotent: a second run finds nothing left."""
        ...


class TableExporter:
    """The common shape: a context lists its tables; reading, the last write
    and the deletion are generic. A context overrides what it adds — its
    documents, its files, its references, a step before the deletion."""

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

    def before_erase(self, db: Session, scope: ExportScope) -> int:
        """A context's own step before its rows go (ends a secret at the
        secrets manager, deletes a stored file). Returns rows it removed."""

        return 0

    def erasable_tables(self, scope: ExportScope) -> list[ExportedTable]:
        return [spec for spec in self.exported_tables() if spec.erase and (scope.sole_in_group or not spec.group_owned)]

    def erase(self, session_factory: Callable[[], Session], scope: ExportScope) -> ErasureResult:
        db = session_factory()
        try:
            deleted = self.before_erase(db, scope)
            specs = {spec.table: spec for spec in self.erasable_tables(scope)}
            # Children before parents: a child's scope may be a sub-select on
            # its parent, which must still be there when the child goes.
            extra = [
                (dependency, spec.table)
                for spec in specs.values()
                for dependency in spec.depends_on
                if dependency in specs
            ]
            for table in reversed(sort_tables(list(specs), extra_dependencies=extra)):
                spec = specs[table]
                clause = (spec.erase_scope or spec.scope)(scope)
                deleted += _rowcount(db.execute(delete(table).where(clause)))
            db.commit()
        finally:
            db.close()
        return ErasureResult(context=self.context, rows_deleted=deleted)


def _rowcount(result: Any) -> int:
    """How many rows a DML statement touched (a CursorResult's count)."""

    return int(getattr(result, "rowcount", 0) or 0)


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


def read_rows(db: Session, spec: ExportedTable, scope: ExportScope) -> tuple[list[str], list[dict[str, Any]]]:
    columns = [column for column in spec.table.c if column.name not in spec.omit_columns]
    order = [spec.table.c[name] for name in ("created_at", "id") if name in spec.table.c]
    statement = select(*columns).where(spec.scope(scope)).order_by(*order)
    rows = [dict(row._mapping) for row in db.execute(statement)]
    return [column.name for column in columns], rows


@dataclasses.dataclass(frozen=True)
class ArchiveSummary:
    data: bytes
    row_counts: dict[str, int]
    document_count: int
    file_count: int


def _entities(
    db: Session, scope: ExportScope, registered: Sequence[Exporter]
) -> Iterator[tuple[Exporter, ExportedTable, list[str], list[dict]]]:
    for exporter in registered:
        for spec in exporter.exported_tables():
            if not spec.in_archive:
                continue
            names, rows = read_rows(db, spec, scope)
            yield exporter, spec, names, rows


def write_archive(
    db: Session,
    *,
    scope: ExportScope,
    produced_at: dt.datetime,
    header: Mapping[str, Any],
    registered: Sequence[Exporter] | None = None,
) -> ArchiveSummary:
    """The export package (FR-A-44): per entity `data/{context}/{table}.csv`
    and `.json`, every issued document under `documents/` with an index of
    numbers and dates, every uploaded file under `files/`, and `schema.json`
    describing each entity's columns and what was left out and why. A ZIP:
    open formats inside an open container."""

    exporters_ = list(registered if registered is not None else exporters())
    buffer = io.BytesIO()
    schema_entities: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    row_counts: dict[str, int] = {}
    document_index: list[dict[str, Any]] = []
    file_index: list[dict[str, Any]] = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for exporter, spec, names, rows in _entities(db, scope, exporters_):
            base = f"data/{exporter.context}/{spec.name}"
            text = io.StringIO()
            writer = csv.writer(text, lineterminator="\n")
            writer.writerow(names)
            for row in rows:
                writer.writerow([_csv_cell(row[name]) for name in names])
            archive.writestr(f"{base}.csv", text.getvalue())
            archive.writestr(
                f"{base}.json",
                json.dumps([{name: plain(row[name]) for name in names} for row in rows], ensure_ascii=False, indent=1),
            )
            row_counts[spec.name] = len(rows)
            schema_entities.append(
                {
                    "context": exporter.context,
                    "entity": spec.name,
                    "files": [f"{base}.csv", f"{base}.json"],
                    "rows": len(rows),
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
    return ArchiveSummary(
        data=buffer.getvalue(), row_counts=row_counts, document_count=len(document_index), file_count=len(file_index)
    )
