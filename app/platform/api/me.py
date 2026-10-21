"""My account (KAN-201; Dealer Administration "My account" — every user,
not only the manager): the caller's own profile, signature and connected
accounts. Self-scoped like `/v1/me/preferences`: no user id in the path,
the caller is the token's `sub`, and being signed in is the only gate.

Sessions and MFA stay under `/v1/auth/sessions` and `/v1/auth/mfa/*`
(KAN-196) — Anto, 2026-10-20: one MFA surface, no moved route. The UI
language stays the `ui` preference. A session that still owes a second
factor reaches none of these routes (the session guard confines it to the
enrolment paths).
"""

import uuid

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.object_store import ObjectStore, get_object_store
from app.db import get_db
from app.platform.schemas.me import ConnectedAccountListRead, ConnectedAccountRead, MeRead, MeUpdate
from app.platform.services import connected_account, signature
from app.platform.services import me as me_service
from app.platform.services import user as user_service

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeRead)
def get_me(principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)):
    return me_service.me_read(db, user_service.get_own_user_or_404(db, principal.user_id))


@router.patch("/me", response_model=MeRead)
def update_me(
    body: MeUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    user = user_service.get_own_user_for_update_or_404(db, principal.user_id)
    check_version(user.version, if_match, entity_name="User")
    user = me_service.update_own_profile(db, user=user, data=body, actor_id=principal.user_id)
    return me_service.me_read(db, user)


@router.put("/me/signature", response_model=MeRead)
def put_my_signature(
    request: Request,
    image: bytes = Body(media_type="image/png", description="The image itself: PNG or JPEG, at most 1 MiB."),
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    """The caller's own signature image — the same store, key and rules as
    the manager's route on the Users screen (KAN-200)."""

    user = user_service.get_own_user_for_update_or_404(db, principal.user_id)
    check_version(user.version, if_match, entity_name="User")
    signature.set_signature(
        db,
        store,
        user=user,
        data=image,
        content_type=request.headers.get("content-type", ""),
        actor_id=principal.user_id,
    )
    return me_service.me_read(db, user)


@router.get("/me/signature")
def get_my_signature(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    stored = signature.get_signature(store, user=user_service.get_own_user_or_404(db, principal.user_id))
    return Response(content=stored.data, media_type=stored.content_type, headers={"Cache-Control": "private, no-store"})


@router.delete("/me/signature", response_model=MeRead)
def delete_my_signature(
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
):
    user = user_service.get_own_user_for_update_or_404(db, principal.user_id)
    check_version(user.version, if_match, entity_name="User")
    signature.remove_signature(db, store, user=user, actor_id=principal.user_id)
    return me_service.me_read(db, user)


@router.get("/me/connected-accounts", response_model=ConnectedAccountListRead)
def list_my_connected_accounts(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """FR-AU-24: the user's own grants. Linking ships with the Outlook
    integration; until then the list is empty."""

    rows = connected_account.list_for_user(db, user_id=principal.user_id)
    return ConnectedAccountListRead(items=[ConnectedAccountRead.model_validate(r, from_attributes=True) for r in rows])


@router.delete("/me/connected-accounts/{account_id}", response_model=ConnectedAccountRead)
def revoke_my_connected_account(
    account_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """The user ends one of their own grants; another user's id is a 404.
    The row stays, revoked."""

    user = user_service.get_own_user_for_update_or_404(db, principal.user_id)  # user row first (KAN-380)
    account = connected_account.get_for_update_or_404(db, user_id=user.id, account_id=account_id)
    check_version(account.version, if_match, entity_name="ConnectedAccount")
    connected_account.revoke(
        db, account=account, user=user, actor_id=principal.user_id, reason=connected_account.REASON_USER
    )
    db.commit()
    connected_account.end_at_providers([account])
    db.refresh(account)
    return ConnectedAccountRead.model_validate(account, from_attributes=True)
