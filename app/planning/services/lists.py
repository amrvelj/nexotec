"""The planner lists of a Betrieb (KAN-808, S7): workplace types,
technician types and qualifications.

A name typed once with optional translations (ruling 176), a place in the
list, and per list its own fields: a technician type's rank and
counted-time factor, a qualification's «mandatory» flag (stored only, Anto
2026-11-25). Names are unique per list, ignoring case.

A workplace or technician type copied from Nexotec's starting set is a
sample (ADR-222) until the manager changes or confirms it: any change, or
the confirmation, ends the sample. An entry a resource uses is not deleted
(409 `planning_list_entry_in_use`); one no resource uses is.

The caller checks the role and the Betrieb (the API: `planner_resources`
matrix row, tenant match, the `aftersales` module) and commits. Every write
is audited and takes the Betrieb's master-data lock first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError
from app.core.text_collation import TextSortKey
from app.planning.models.lists import Qualification, TechnicianType, WorkplaceType
from app.planning.models.resource import Resource, ResourceQualification
from app.planning.services.locks import lock_master_data


@dataclass(frozen=True)
class ListSpec:
    """What differs between the three lists."""

    model: Any
    entity_type: str
    # The fields of this list beyond name, translations and sort order.
    figures: tuple[str, ...]
    has_sample: bool


WORKPLACE_TYPES = ListSpec(WorkplaceType, "planning_workplace_type", (), True)
TECHNICIAN_TYPES = ListSpec(TechnicianType, "planning_technician_type", ("rank", "counted_time_factor"), True)
QUALIFICATIONS = ListSpec(Qualification, "planning_qualification", ("is_mandatory",), False)

_BASE_FIELDS = ("name", "name_translations", "sort_order")


def clean_name(name: str) -> str:
    return " ".join(name.split())


def clean_translations(translations: dict[str, str] | None) -> dict[str, str] | None:
    """Only the languages given (ruling 176); an empty one is dropped."""

    if not translations:
        return None
    cleaned = {str(getattr(language, "value", language)): clean_name(text or "") for language, text in translations.items()}
    return {language: text for language, text in sorted(cleaned.items()) if text} or None


def list_entries(db: Session, spec: ListSpec, *, dealership_id: uuid.UUID) -> list[Any]:
    model = spec.model
    return list(
        db.scalars(
            select(model)
            .where(model.tenant_id == dealership_id)
            .order_by(model.sort_order, TextSortKey(model.name), model.id)
        )
    )


def get_entry_or_404(
    db: Session, spec: ListSpec, *, dealership_id: uuid.UUID, entry_id: uuid.UUID, lock: bool = False
) -> Any:
    model = spec.model
    stmt = select(model).where(model.id == entry_id, model.tenant_id == dealership_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    entry = db.scalar(stmt)
    if entry is None:
        raise NotFoundError("The list entry was not found.")
    return entry


def _audit(spec: ListSpec, entry: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "name": entry.name,
        "nameTranslations": entry.name_translations,
        "sortOrder": entry.sort_order,
    }
    if spec.has_sample:
        snapshot["isSample"] = entry.is_sample
        snapshot["seedCode"] = entry.seed_code
    for field in spec.figures:
        value = getattr(entry, field)
        snapshot[_camel(field)] = str(value) if isinstance(value, Decimal) else value
    return snapshot


def _camel(field: str) -> str:
    head, *rest = field.split("_")
    return head + "".join(part.title() for part in rest)


def _refuse_taken_name(
    db: Session, spec: ListSpec, dealership_id: uuid.UUID, name: str, *, other_than: uuid.UUID | None = None
) -> None:
    model = spec.model
    stmt = select(model.id).where(model.tenant_id == dealership_id, func.lower(model.name) == name.lower())
    if other_than is not None:
        stmt = stmt.where(model.id != other_than)
    if db.scalar(stmt.limit(1)) is not None:
        raise ConflictError(
            "The list already has an entry of this name.", details={"reason": "planning_list_entry_name_taken"}
        )


def create_entry(
    db: Session,
    spec: ListSpec,
    *,
    dealership_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    name: str,
    name_translations: dict[str, str] | None = None,
    sort_order: int = 0,
    **figures: Any,
) -> Any:
    unknown = set(figures) - set(spec.figures)
    if unknown:
        raise TypeError(f"{spec.entity_type} has no {sorted(unknown)}")
    cleaned = clean_name(name)
    lock_master_data(db, dealership_id)
    _refuse_taken_name(db, spec, dealership_id, cleaned)
    entry = spec.model(
        tenant_id=dealership_id,
        name=cleaned,
        name_translations=clean_translations(name_translations),
        sort_order=sort_order,
        created_by=actor_id,
        updated_by=actor_id,
        **figures,
    )
    db.add(entry)
    db.flush()
    record_audit_event(
        db,
        entity_type=spec.entity_type,
        entity_id=entry.id,
        tenant_id=dealership_id,
        action="create",
        actor_id=actor_id,
        after=_audit(spec, entry),
    )
    return entry


def change_entry(
    db: Session,
    spec: ListSpec,
    *,
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
    changes: dict[str, Any],
) -> Any:
    """Applies the fields in `changes` (the PATCH's fields set). A change
    ends a sample (ADR-222); a PATCH that changes nothing writes nothing."""

    unknown = set(changes) - set(_BASE_FIELDS) - set(spec.figures)
    if unknown:
        raise TypeError(f"{spec.entity_type} has no {sorted(unknown)}")
    lock_master_data(db, dealership_id)
    entry = get_entry_or_404(db, spec, dealership_id=dealership_id, entry_id=entry_id, lock=True)
    check_version(entry.version, expected_version, entity_name="List entry")
    before = _audit(spec, entry)
    for field, value in changes.items():
        if field == "name":
            if value is None:
                continue
            value = clean_name(value)
            _refuse_taken_name(db, spec, dealership_id, value, other_than=entry.id)
        elif field == "name_translations":
            value = clean_translations(value)
        elif field in {"sort_order", "is_mandatory"} and value is None:
            # Not nullable: an explicit null leaves them as they are.
            continue
        setattr(entry, field, value)
    if _audit(spec, entry) == before:
        return entry
    if spec.has_sample:
        entry.is_sample = False
    entry.version += 1
    entry.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type=spec.entity_type,
        entity_id=entry.id,
        tenant_id=dealership_id,
        action="update",
        actor_id=actor_id,
        before=before,
        after=_audit(spec, entry),
    )
    return entry


def confirm_entry(
    db: Session,
    spec: ListSpec,
    *,
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
) -> Any:
    """«Bestätigen»: the manager keeps a sample as it is — it becomes the
    Betrieb's own entry. Confirming an entry that is no sample changes nothing."""

    if not spec.has_sample:
        raise TypeError(f"{spec.entity_type} has no samples")
    lock_master_data(db, dealership_id)
    entry = get_entry_or_404(db, spec, dealership_id=dealership_id, entry_id=entry_id, lock=True)
    check_version(entry.version, expected_version, entity_name="List entry")
    if not entry.is_sample:
        return entry
    before = _audit(spec, entry)
    entry.is_sample = False
    entry.version += 1
    entry.updated_by = actor_id
    db.flush()
    record_audit_event(
        db,
        entity_type=spec.entity_type,
        entity_id=entry.id,
        tenant_id=dealership_id,
        action="confirm",
        actor_id=actor_id,
        before=before,
        after=_audit(spec, entry),
    )
    return entry


def _in_use(db: Session, spec: ListSpec, entry: Any) -> bool:
    if spec is WORKPLACE_TYPES:
        stmt = select(Resource.id).where(Resource.workplace_type_id == entry.id)
    elif spec is TECHNICIAN_TYPES:
        stmt = select(Resource.id).where(Resource.technician_type_id == entry.id)
    else:
        stmt = select(ResourceQualification.id).where(ResourceQualification.qualification_id == entry.id)
    return db.scalar(stmt.limit(1)) is not None


def _refuse_in_use() -> ConflictError:
    return ConflictError(
        "A resource uses this entry; change the resource first.", details={"reason": "planning_list_entry_in_use"}
    )


def delete_entry(
    db: Session,
    spec: ListSpec,
    *,
    dealership_id: uuid.UUID,
    entry_id: uuid.UUID,
    expected_version: int,
    actor_id: uuid.UUID | None,
) -> None:
    """Deletes an entry no resource uses, an active or a deactivated one."""

    lock_master_data(db, dealership_id)
    entry = get_entry_or_404(db, spec, dealership_id=dealership_id, entry_id=entry_id, lock=True)
    check_version(entry.version, expected_version, entity_name="List entry")
    if _in_use(db, spec, entry):
        raise _refuse_in_use()
    before = _audit(spec, entry)
    db.delete(entry)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise _refuse_in_use() from None
    record_audit_event(
        db,
        entity_type=spec.entity_type,
        entity_id=entry_id,
        tenant_id=dealership_id,
        action="delete",
        actor_id=actor_id,
        before=before,
    )


def entries_of_the_betrieb(
    db: Session, spec: ListSpec, *, dealership_id: uuid.UUID, entry_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    """The ids among `entry_ids` that name an entry of this Betrieb's list."""

    if not entry_ids:
        return set()
    model = spec.model
    return set(db.scalars(select(model.id).where(model.tenant_id == dealership_id, model.id.in_(entry_ids))))
