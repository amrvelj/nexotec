"""Reference-data endpoints (issue #3): admin-managed business taxonomy.

GET is open to any authenticated principal — sales/inventory need these
lists to populate dropdowns when creating Vehicle/Customer records, and the
data itself isn't tenant-scoped or sensitive.

POST/PATCH are platform_admin-only, not dealer_admin — overriding the spec
text's "dealer_admin/platform_admin manageable" (CTO ruling, 2026-08-06).
This table has no tenant partition at all: a dealer_admin renaming,
deactivating, or reordering a shared value would have blast radius across
every other tenant's dropdowns, with nothing to contain it (unlike Vehicle,
which at least has per-tenant custody events).
"""

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.idempotency import find_cached_response, store_response
from app.core.pagination import PageParams, page_params
from app.db import get_db
from app.platform.schemas.reference_data import (
    ReferenceListCollection,
    ReferenceListRead,
    ReferenceValueCreate,
    ReferenceValuePage,
    ReferenceValueRead,
    ReferenceValueUpdate,
)
from app.platform.services import reference_data as reference_data_service

router = APIRouter(tags=["reference-data"])


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


@router.get("/reference-data", response_model=ReferenceListCollection)
def list_reference_lists(
    principal: Principal = Depends(get_current_principal),  # any authenticated user may read
    db: Session = Depends(get_db),
):
    """Enumerate the canonical reference lists.

    Open to any authenticated principal, exactly like GET
    /reference-data/{list_code}: sales/inventory need the set to build the
    `/settings/reference` list picker and their own dropdowns. A plain
    wrapped collection, not a page — the set is fixed and seed-only.
    """

    rows = reference_data_service.list_reference_lists(db)
    return ReferenceListCollection(
        items=[
            ReferenceListRead(
                list_code=row["list"].list_code,
                label_de=row["list"].label_de,
                label_fr=row["list"].label_fr,
                label_it=row["list"].label_it,
                label_en=row["list"].label_en,
                value_count=row["value_count"],
                active_value_count=row["active_value_count"],
                created_at=row["list"].created_at,
                updated_at=row["list"].updated_at,
            )
            for row in rows
        ]
    )


@router.get("/reference-data/{list_code}", response_model=ReferenceValuePage)
def list_reference_values(
    list_code: str,
    active: bool | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    ref_list = reference_data_service.get_reference_list_or_404(db, list_code)
    rows, next_cursor = reference_data_service.list_reference_values(
        db, list_id=ref_list.id, active=active, params=params
    )
    return ReferenceValuePage(
        items=[ReferenceValueRead.model_validate(v, from_attributes=True) for v in rows], next_cursor=next_cursor
    )


@router.post("/reference-data/{list_code}", response_model=ReferenceValueRead, status_code=201)
def create_reference_value(
    list_code: str,
    body: ReferenceValueCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    ref_list = reference_data_service.get_reference_list_or_404(db, list_code)

    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    value = reference_data_service.create_reference_value(
        db, ref_list=ref_list, data=body, actor_id=principal.user_id
    )
    result = ReferenceValueRead.model_validate(value, from_attributes=True)

    if idempotency_key:
        store_response(
            db,
            tenant_id=principal.tenant_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.patch("/reference-data/{list_code}/{value_code}", response_model=ReferenceValueRead)
def update_reference_value(
    list_code: str,
    value_code: str,
    body: ReferenceValueUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    ref_list = reference_data_service.get_reference_list_or_404(db, list_code)
    value = reference_data_service.get_reference_value_or_404(db, list_id=ref_list.id, value_code=value_code)
    check_version(value.version, if_match, entity_name="ReferenceValue")
    value = reference_data_service.update_reference_value(db, value=value, data=body, actor_id=principal.user_id)
    return ReferenceValueRead.model_validate(value, from_attributes=True)
