"""Dealer + User bootstrap endpoints (issue #2).

POST /v1/dealers is platform-admin only — it's how the first tenant gets
created. POST /v1/dealers/{id}/users creates the initial admin User for that
Dealer (also usable by an existing dealer_admin to add staff within their
own tenant). See PLANS/DMS_MDM_V1_SPEC.md §3 + the Swiss addendum.
"""

import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.audit import list_tenant_audit_events
from app.core.audit_schemas import AuditEventPage, AuditEventRead
from app.core.auth import AccessRole, Principal, get_current_principal, require_access_role
from app.core.concurrency import check_version, require_if_match
from app.core.idempotency import find_cached_response, store_response
from app.core.pagination import PageParams, page_params
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.platform.models.dealer import DealerStatus
from app.platform.models.user import UserStatus
from app.platform.schemas.dealer import DealerCreate, DealerPage, DealerRead, DealerUpdate
from app.platform.schemas.user import UserCreate, UserPage, UserRead, UserUpdate
from app.platform.services import dealer as dealer_service
from app.platform.services import user as user_service

router = APIRouter(tags=["dealers"])


def _idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    return idempotency_key


# --- Dealer ------------------------------------------------------------


@router.post("/dealers", response_model=DealerRead, status_code=201)
def create_dealer(
    body: DealerCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=principal.tenant_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    dealer = dealer_service.create_dealer(db, data=body, actor_id=principal.user_id)
    result = DealerRead.model_validate(dealer, from_attributes=True)

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


@router.get("/dealers/{dealer_id}", response_model=DealerRead)
def get_dealer(
    dealer_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    dealer = dealer_service.get_dealer_or_404(db, dealer_id)
    return DealerRead.model_validate(dealer, from_attributes=True)


@router.patch("/dealers/{dealer_id}", response_model=DealerRead)
def update_dealer(
    dealer_id: uuid.UUID,
    body: DealerUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    dealer = dealer_service.get_dealer_or_404(db, dealer_id)
    check_version(dealer.version, if_match, entity_name="Dealer")
    dealer = dealer_service.update_dealer(db, dealer=dealer, data=body, actor_id=principal.user_id)
    return DealerRead.model_validate(dealer, from_attributes=True)


@router.get("/dealers", response_model=DealerPage)
def list_dealers(
    status: DealerStatus | None = None,
    parent_group_id: uuid.UUID | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role()),  # platform_admin only
    db: Session = Depends(get_db),
):
    rows, next_cursor = dealer_service.list_dealers(
        db, params=params, status=status, parent_group_id=parent_group_id
    )
    return DealerPage(
        items=[DealerRead.model_validate(d, from_attributes=True) for d in rows], next_cursor=next_cursor
    )


@router.get("/dealers/{dealer_id}/audit-log", response_model=AuditEventPage)
def get_dealer_audit_log(
    dealer_id: uuid.UUID,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN, AccessRole.AUDITOR)),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    rows, next_cursor = list_tenant_audit_events(db, tenant_id=dealer_id, params=params)
    return AuditEventPage(
        items=[AuditEventRead.model_validate(e, from_attributes=True) for e in rows], next_cursor=next_cursor
    )


# --- User ----------------------------------------------------------------


@router.post("/dealers/{dealer_id}/users", response_model=UserRead, status_code=201)
def create_user(
    dealer_id: uuid.UUID,
    body: UserCreate,
    request: Request,
    idempotency_key: str | None = Depends(_idempotency_key),
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    dealer_service.get_dealer_or_404(db, dealer_id)  # 404s a well-formed but nonexistent dealer_id

    request_body = body.model_dump(mode="json", by_alias=True)
    if idempotency_key:
        cached = find_cached_response(
            db, tenant_id=dealer_id, key=idempotency_key, path=request.url.path, body=request_body
        )
        if cached is not None:
            return JSONResponse(status_code=cached.response_status, content=cached.response_body)

    user = user_service.create_user(db, dealer_id=dealer_id, data=body, actor_id=principal.user_id)
    result = UserRead.model_validate(user, from_attributes=True)

    if idempotency_key:
        store_response(
            db,
            tenant_id=dealer_id,
            key=idempotency_key,
            path=request.url.path,
            body=request_body,
            response_status=201,
            response_body=result.model_dump(mode="json", by_alias=True),
        )
        db.commit()
    return result


@router.get("/dealers/{dealer_id}/users/{user_id}", response_model=UserRead)
def get_user(
    dealer_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    user = user_service.get_user_or_404(db, dealer_id, user_id)
    return UserRead.model_validate(user, from_attributes=True)


@router.patch("/dealers/{dealer_id}/users/{user_id}", response_model=UserRead)
def update_user(
    dealer_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UserUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_access_role(AccessRole.DEALER_ADMIN)),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    user = user_service.get_user_or_404(db, dealer_id, user_id)
    check_version(user.version, if_match, entity_name="User")
    user = user_service.update_user(db, user=user, data=body, actor_id=principal.user_id)
    return UserRead.model_validate(user, from_attributes=True)


@router.get("/dealers/{dealer_id}/users", response_model=UserPage)
def list_users(
    dealer_id: uuid.UUID,
    role: str | None = None,
    status: UserStatus | None = None,
    params: PageParams = Depends(page_params),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealer_id, principal)
    rows, next_cursor = user_service.list_users(db, dealer_id=dealer_id, role=role, status=status, params=params)
    return UserPage(
        items=[UserRead.model_validate(u, from_attributes=True) for u in rows], next_cursor=next_cursor
    )
