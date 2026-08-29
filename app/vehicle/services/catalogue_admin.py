"""Master-data admin service layer (WP-5 PR-8, FR-V-11): brands CRUD and
the mapping-gap queue. Reference-value editing itself reuses the existing
app.platform.services.reference_data unchanged — nothing new needed there.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams, build_page, paginate_query
from app.vehicle.models.catalogue import Brand
from app.vehicle.models.provider import MappingGap
from app.vehicle.schemas.catalogue import BrandCreate, BrandUpdate
from app.vehicle.services.provider import resolve_mapping_gap

_AUDITED_BRAND_FIELDS = {"display_name"}


def get_brand_or_404(db: Session, brand_id: uuid.UUID) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise NotFoundError(f"Brand {brand_id} was not found.")
    return brand


def list_brands(db: Session, *, params: PageParams) -> tuple[list[Brand], str | None]:
    stmt = paginate_query(select(Brand), model=Brand, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def create_brand(db: Session, *, data: BrandCreate, actor_id: uuid.UUID) -> Brand:
    brand = Brand(code=data.code, display_name=data.display_name, created_by=actor_id, updated_by=actor_id)
    db.add(brand)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"A brand with code '{data.code}' already exists.", details={"code": data.code}) from exc

    record_audit_event(
        db, entity_type="vehicle_brand", entity_id=brand.id, tenant_id=None, action="create", actor_id=actor_id,
        after={"code": brand.code, "displayName": brand.display_name},
    )
    db.commit()
    db.refresh(brand)
    return brand


def update_brand(db: Session, *, brand: Brand, data: BrandUpdate, actor_id: uuid.UUID) -> Brand:
    changes = data.model_dump(exclude_unset=True)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field, value in changes.items():
        current = getattr(brand, field)
        if current == value:
            continue
        if field in _AUDITED_BRAND_FIELDS:
            before[field] = current
            after[field] = value
        setattr(brand, field, value)

    brand.updated_by = actor_id
    brand.version += 1

    if before or after:
        record_audit_event(
            db, entity_type="vehicle_brand", entity_id=brand.id, tenant_id=None, action="update", actor_id=actor_id,
            before=before or None, after=after or None,
        )
    db.commit()
    db.refresh(brand)
    return brand


def get_mapping_gap_or_404(db: Session, gap_id: uuid.UUID) -> MappingGap:
    gap = db.get(MappingGap, gap_id)
    if gap is None:
        raise NotFoundError(f"Mapping gap {gap_id} was not found.")
    return gap


def list_mapping_gaps(db: Session, *, resolved: bool | None, params: PageParams) -> tuple[list[MappingGap], str | None]:
    stmt = select(MappingGap)
    if resolved is not None:
        stmt = stmt.where(MappingGap.resolved == resolved)
    stmt = paginate_query(stmt, model=MappingGap, params=params)
    rows = list(db.scalars(stmt).all())
    return build_page(rows, params)


def resolve_gap(
    db: Session, *, gap: MappingGap, canonical_list_code: str, canonical_value_code: str, actor_id: uuid.UUID
) -> MappingGap:
    gap = resolve_mapping_gap(
        db, gap=gap, canonical_list_code=canonical_list_code, canonical_value_code=canonical_value_code,
        actor_id=actor_id,
    )
    record_audit_event(
        db, entity_type="vehicle_mapping_gap", entity_id=gap.id, tenant_id=None, action="resolve", actor_id=actor_id,
        after={
            "provider": gap.provider, "providerCode": gap.provider_code,
            "canonicalListCode": canonical_list_code, "canonicalValueCode": canonical_value_code,
        },
    )
    db.commit()
    db.refresh(gap)
    return gap
