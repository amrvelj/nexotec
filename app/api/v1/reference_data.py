"""Reference-data endpoints (issue #3): admin-managed business taxonomy.

GET is open to any authenticated principal — sales/inventory need these
lists to populate dropdowns when creating Vehicle/Customer records, and the
data itself isn't tenant-scoped or sensitive. Only POST/PATCH (mutating the
shared taxonomy) are restricted to dealer_admin/platform_admin per the spec.
Flagged in the PR: any dealer_admin, not just the seeding platform_admin,
can mutate this platform-wide data — that's what Round 2 Q3 says
("dealer_admin/platform_admin manageable"), but it's worth a second look
since it lets one tenant's admin affect every tenant's dropdowns.
"""

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.pagination import PageParams, page_params
from app.db import get_db
from app.schemas.reference_data import (
    ReferenceValueCreate,
    ReferenceValuePage,
    ReferenceValueRead,
    ReferenceValueUpdate,
)
from app.services import reference_data as reference_data_service
from app.services.idempotency import find_cached_response, store_response

router = APIRouter(tags=["reference-data"])


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


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
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
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
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
    db: Session = Depends(get_db),
):
    ref_list = reference_data_service.get_reference_list_or_404(db, list_code)
    value = reference_data_service.get_reference_value_or_404(db, list_id=ref_list.id, value_code=value_code)
    check_version(value.version, if_match, entity_name="ReferenceValue")
    value = reference_data_service.update_reference_value(db, value=value, data=body, actor_id=principal.user_id)
    return ReferenceValueRead.model_validate(value, from_attributes=True)
