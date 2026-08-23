"""Reference-data service layer: list_code-scoped ReferenceValue CRUD.

ReferenceList rows are seed-only for v1 (see app/models/reference_data.py) —
there is no create-a-new-list operation here, only lookup by list_code plus
create/update of the ReferenceValue rows within an existing list.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.schemas.reference_data import ReferenceValueCreate, ReferenceValueUpdate
from app.core.audit import record_audit_event

_AUDITED_FIELDS = {"label_de", "label_fr", "label_it", "label_en", "sort_order", "active"}


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def get_reference_list_or_404(db: Session, list_code: str) -> ReferenceList:
    ref_list = db.scalar(select(ReferenceList).where(ReferenceList.list_code == list_code))
    if ref_list is None:
        raise NotFoundError(f"Reference list '{list_code}' was not found.")
    return ref_list


def get_reference_value_or_404(db: Session, *, list_id: uuid.UUID, value_code: str) -> ReferenceValue:
    value = db.scalar(
        select(ReferenceValue)
        .options(joinedload(ReferenceValue.list))
        .where(ReferenceValue.list_id == list_id, ReferenceValue.value_code == value_code)
    )
    if value is None:
        raise NotFoundError(f"Reference value '{value_code}' was not found.")
    return value


def list_reference_values(
    db: Session, *, list_id: uuid.UUID, active: bool | None, params: PageParams
) -> tuple[list[ReferenceValue], str | None]:
    stmt = select(ReferenceValue).options(joinedload(ReferenceValue.list)).where(ReferenceValue.list_id == list_id)
    if active is not None:
        stmt = stmt.where(ReferenceValue.active == active)
    stmt = paginate_query(stmt, model=ReferenceValue, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def create_reference_value(
    db: Session, *, ref_list: ReferenceList, data: ReferenceValueCreate, actor_id: uuid.UUID
) -> ReferenceValue:
    value = ReferenceValue(
        list_id=ref_list.id,
        value_code=data.value_code,
        label_de=data.label_de,
        label_fr=data.label_fr,
        label_it=data.label_it,
        label_en=data.label_en,
        sort_order=data.sort_order,
        active=data.active,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"Reference value '{data.value_code}' already exists in list '{ref_list.list_code}'.",
            details={"listCode": ref_list.list_code, "valueCode": data.value_code},
        ) from exc

    record_audit_event(
        db,
        entity_type="reference_value",
        entity_id=value.id,
        tenant_id=None,
        action="create",
        actor_id=actor_id,
        after={
            "list_code": ref_list.list_code,
            "value_code": value.value_code,
            "label_de": value.label_de,
            "label_fr": value.label_fr,
            "label_it": value.label_it,
            "label_en": value.label_en,
            "sort_order": value.sort_order,
            "active": value.active,
        },
    )
    db.commit()
    db.refresh(value)
    return value


def update_reference_value(
    db: Session, *, value: ReferenceValue, data: ReferenceValueUpdate, actor_id: uuid.UUID
) -> ReferenceValue:
    changes = data.model_dump(exclude_unset=True)

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for field, new_value in changes.items():
        current = getattr(value, field)
        if current == new_value:
            continue
        if field in _AUDITED_FIELDS:
            before[field] = _plain(current)
            after[field] = _plain(new_value)
        setattr(value, field, new_value)

    value.updated_by = actor_id
    value.version += 1

    if before or after:
        record_audit_event(
            db,
            entity_type="reference_value",
            entity_id=value.id,
            tenant_id=None,
            action="update",
            actor_id=actor_id,
            before=before or None,
            after=after or None,
        )

    db.commit()
    db.refresh(value)
    return value
