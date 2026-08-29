"""Master-data admin endpoints (WP-5 PR-8, FR-V-11) — platform_admin only,
same gate as app.platform.api.reference_data's own write endpoints. Brands
aren't reference-data-shaped (no per-language label, too high-cardinality)
so they get their own small CRUD surface here rather than being folded
into reference_data's.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.pagination import PageParams, page_params
from app.db import get_db
from app.vehicle.schemas.catalogue import (
    BrandCreate,
    BrandPage,
    BrandRead,
    BrandUpdate,
    MappingGapPage,
    MappingGapRead,
    MappingGapResolve,
)
from app.vehicle.services import catalogue_admin

router = APIRouter(tags=["vehicle-mdm-admin"])


@router.get("/vehicle-mdm/brands", response_model=BrandPage)
def list_brands(
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),  # any authenticated user may read
    db: Session = Depends(get_db),
):
    rows, next_cursor = catalogue_admin.list_brands(db, params=params)
    return BrandPage(items=[BrandRead.model_validate(b, from_attributes=True) for b in rows], next_cursor=next_cursor)


@router.post("/vehicle-mdm/brands", response_model=BrandRead, status_code=201)
def create_brand(
    body: BrandCreate,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    brand = catalogue_admin.create_brand(db, data=body, actor_id=principal.user_id)
    return BrandRead.model_validate(brand, from_attributes=True)


@router.patch("/vehicle-mdm/brands/{brand_id}", response_model=BrandRead)
def update_brand(
    brand_id: uuid.UUID,
    body: BrandUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    brand = catalogue_admin.get_brand_or_404(db, brand_id)
    check_version(brand.version, if_match, entity_name="Brand")
    brand = catalogue_admin.update_brand(db, brand=brand, data=body, actor_id=principal.user_id)
    return BrandRead.model_validate(brand, from_attributes=True)


@router.get("/vehicle-mdm/mapping-gaps", response_model=MappingGapPage)
def list_mapping_gaps(
    resolved: bool | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only — the admin queue itself
    db: Session = Depends(get_db),
):
    rows, next_cursor = catalogue_admin.list_mapping_gaps(db, resolved=resolved, params=params)
    return MappingGapPage(
        items=[MappingGapRead.model_validate(g, from_attributes=True) for g in rows], next_cursor=next_cursor
    )


@router.post("/vehicle-mdm/mapping-gaps/{gap_id}/resolve", response_model=MappingGapRead)
def resolve_mapping_gap(
    gap_id: uuid.UUID,
    body: MappingGapResolve,
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    gap = catalogue_admin.get_mapping_gap_or_404(db, gap_id)
    gap = catalogue_admin.resolve_gap(
        db, gap=gap, canonical_list_code=body.canonical_list_code, canonical_value_code=body.canonical_value_code,
        actor_id=principal.user_id,
    )
    return MappingGapRead.model_validate(gap, from_attributes=True)
